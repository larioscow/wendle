"""Inter-step injection hooks (increment 1): firing + keying + cont/stop/emit + honest failure.

A hook is a developer callable that runs at a verified replay boundary (keyed by step index or the
observed screen namespace), reaches the device only via ctx.driver, and steers the replay by
returning cont()/stop()/[goto = increment 2]. A hook that RAISES or returns stop() halts HONESTLY
with a VALUE-FREE reason — never barrels on. ctx.emit surfaces values via ReplayResult.data.
"""
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import NavOutcome
from wendle.replay.engine import ReplayEngine
from wendle.replay.hooks import HookRegistry, cont, goto, stop

NEXT2 = Action(selector=Selector("text", "Next2"), action_type="click")


def _graph2():
    # S0 -(Continuar)-> S1 -(Next2)-> S2(leaf). For goto branching tests.
    g = _graph()
    g.upsert_screen(Screen(id="S2", namespace="app/.C", package="app", activity=".C"))
    g.add_transition(Transition(source="S1", target="S2", action=NEXT2))
    return g

LAUNCH_XML = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
              'content-desc=""><node class="android.widget.Button" resource-id="app:id/root" '
              'clickable="true" content-desc="" text="x"/></node></hierarchy>')
GO = Action(selector=Selector("text", "Continuar"), action_type="click")


def _graph():
    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace="app/.A", package="app", activity=".A",
                           structure_id=structure_id("app/.A", LAUNCH_XML),
                           force_action=ForceAction("am_start", "app/.A", verified_fp="S0")))
    g.upsert_screen(Screen(id="S1", namespace="app/.B", package="app", activity=".B"))
    g.add_transition(Transition(source="S0", target="S1", action=GO))
    return g


def _eng(g, drv):
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                       lookup_timeout=5.0, retry_timeout=1.0)
    eng._observe = lambda: (LAUNCH_XML, "app/.A", "app", True)
    return eng


def _drv():
    return FakeDriver(present_selectors={("text", "Continuar")})


def test_after_hook_fires_with_context_and_continues():
    drv = _drv()
    seen = []
    def h(ctx):
        seen.append((ctx.phase, ctx.step_index, ctx.namespace))
        return None  # None == cont()
    out = _eng(_graph(), drv).run(hooks={"after:0": [h]})
    assert out.status == "completed"
    assert seen == [("after", 0, "app/.A")]
    assert ("text", "Continuar", "click") in drv.taps  # the command ran, hook continued


def test_before_hook_fires_before_the_command():
    drv = _drv()
    order = []
    out = _eng(_graph(), drv).run(hooks={"before:0": [lambda ctx: order.append("hook") or cont()]})
    assert out.status == "completed" and order == ["hook"]


def test_screen_namespace_keying_fires():
    drv = _drv()
    fired = []
    out = _eng(_graph(), drv).run(hooks={"screen:app/.A": [lambda ctx: fired.append(1) or cont()]})
    assert out.status == "completed" and fired  # matched the observed foreground namespace


def test_hook_stop_halts_honestly_before_the_command():
    drv = _drv()
    out = _eng(_graph(), drv).run(hooks={"before:0": [lambda ctx: stop("paywall_undecided")]})
    assert out.status == "stopped"
    assert out.failed_step.kind == "hook" and out.failed_step.error == "hook_stop:paywall_undecided"
    assert ("text", "Continuar", "click") not in drv.taps  # never ran the command


def test_hook_that_raises_stops_honestly_value_free():
    drv = _drv()
    def boom(ctx):
        raise RuntimeError("card 4111111111111111")  # an exception that touched a secret
    out = _eng(_graph(), drv).run(hooks={"before:0": [boom]})
    assert out.status == "stopped"
    assert out.failed_step.error == "hook_failed:boom"   # the function name, NOT the exception text
    assert "4111" not in (out.failed_step.error or "")    # value-free: the secret never leaks
    assert ("text", "Continuar", "click") not in drv.taps  # never barreled on


def test_emit_surfaces_in_result_data():
    drv = _drv()
    def extract(ctx):
        ctx.emit("native_balance", 4200)
        return cont()
    out = _eng(_graph(), drv).run(hooks={"after:0": [extract]})
    assert out.status == "completed" and out.data == {"native_balance": 4200}


def test_first_non_cont_directive_wins():
    drv = _drv()
    calls = []
    def a(ctx):
        calls.append("a"); return stop("a_decided")
    def b(ctx):
        calls.append("b"); return cont()  # must NOT run — a already halted
    out = _eng(_graph(), drv).run(hooks={"before:0": [a, b]})
    assert out.status == "stopped" and out.failed_step.error == "hook_stop:a_decided"
    assert calls == ["a"]


def test_goto_navigates_then_resumes_from_the_target():
    # a branch: goto S1 -> the navigator pathfinds+verifies there -> the linear replay RESUMES from
    # S1 (running S1->S2), and the original S0->S1 command is skipped (we branched past it).
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", tier="EXACT", expected_id=to)
    out = eng.run(hooks={"before:0": [lambda ctx: goto("S1")]})
    assert out.status == "completed"
    assert ("text", "Next2", "click") in drv.taps           # resumed from S1 -> ran S1->S2
    assert ("text", "Continuar", "click") not in drv.taps   # the original S0->S1 step was branched past
    assert any(s.kind == "goto" and s.ok for s in out.steps)


def test_goto_unreachable_stops_honestly():
    g = _graph2()
    eng = _eng(g, _drv())
    eng._nav.navigate = lambda frm, to: NavOutcome("no_route", expected_id=to)
    out = eng.run(hooks={"before:0": [lambda ctx: goto("S2")]})
    assert out.status == "stopped" and out.failed_step.kind == "goto"
    # the navigator's typed outcome, surfaced honestly under the goto_failed prefix (so the
    # replay classifier maps it instead of falling to OTHER)
    assert out.failed_step.error == "goto_failed:no_route"


def test_goto_to_leaf_is_typed_terminal_not_false_completed():
    # the cardinal-honesty fix: a goto whose target (S2) has NO recorded continuation must NOT
    # fall through to a bare "completed" (which means "reproduced the whole capture").
    g = _graph2()  # S2 is a leaf
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    out = eng.run(hooks={"before:0": [lambda ctx: goto("S2")]})
    assert out.status == "stopped"  # NOT "completed"
    assert out.failed_step.error == "resume_empty:S2"


def test_goto_nonexistent_node_is_honest_no_route():
    out = _eng(_graph2(), _drv()).run(hooks={"before:0": [lambda ctx: goto("NOPE")]})
    assert out.status == "stopped" and out.failed_step.error == "goto_no_route:NOPE"


def test_goto_budget_exhausts_on_a_loop():
    # a BACKWARDS goto (the target's departure position <= the issuing step) is a genuine loop:
    # after:1 -> goto(S1) resumes AT command 1, runs it, re-fires after:1, ... The per-run goto
    # budget makes it HALT honestly instead of hanging. (The old vector — an after:0 hook
    # re-keying onto the REBASED flow — is structurally impossible now that goto resumes at
    # original-flow indices; that, not looping, was review finding 14.)
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    out = eng.run(hooks={"after:1": [lambda ctx: goto("S1")]})
    assert out.status == "stopped" and out.failed_step.error == "goto_budget_exhausted"


# ---- hook semantics v2: edge-triggered screen hooks + strict node keying + index-true goto ----

NEXT3 = Action(selector=Selector("text", "Next3"), action_type="click")
ALL3 = {("text", "Continuar"), ("text", "Next2"), ("text", "Next3")}


def _graph3():
    g = _graph2()
    g.upsert_screen(Screen(id="S3", namespace="app/.D", package="app", activity=".D"))
    g.add_transition(Transition(source="S2", target="S3", action=NEXT3))
    return g


def _eng_obs(g, drv, obs):
    """_eng with a CALLABLE observation (lets a test script what each boundary sees)."""
    eng = _eng(g, drv)
    eng._observe = obs
    return eng


def test_screen_hook_fires_once_per_visit():
    # the contract is EDGE-triggered ("fires when that screen is reached"); level-triggered
    # firing ran a side-effectful hook at every before+after boundary — 2N firings across an
    # N-step same-screen visit (review finding 1).
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    fired = []
    out = _eng(_graph2(), drv).run(hooks={"screen:app/.A": [lambda ctx: fired.append(1) or cont()]})
    assert out.status == "completed"
    assert len(fired) == 1  # was 4 (before/after x 2 steps)


def test_screen_hook_rearms_on_reentry():
    # leaving the screen and coming back IS a new arrival — the hook re-fires.
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    obs = lambda: (LAUNCH_XML, "app/.B" if len(drv.taps) == 1 else "app/.A", "app", True)
    fired = []
    out = _eng_obs(_graph2(), drv, obs).run(
        hooks={"screen:app/.A": [lambda ctx: fired.append(1) or cont()]})
    assert out.status == "completed"
    assert len(fired) == 2  # the launch arrival + the re-entry after leaving to app/.B


def test_unresolved_observation_does_not_rearm_screen_hook():
    # a falsy observation (unsettled / ambiguous / off-graph moment) is NO SIGNAL: it must not
    # reset the tracker, so A -> unresolved -> A is ONE visit. (Adversarial blocker 1: volatile
    # cur churn V,None,V re-fired a side-effectful Frida hook mid-visit.)
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    obs = lambda: (LAUNCH_XML, "" if len(drv.taps) == 1 else "app/.A", "app", True)
    fired = []
    out = _eng_obs(_graph2(), drv, obs).run(
        hooks={"screen:app/.A": [lambda ctx: fired.append(1) or cont()]})
    assert out.status == "completed"
    assert len(fired) == 1  # the "" gap never re-armed the edge


def test_screen_hooks_rearm_across_runs():
    # the edge tracker is PER-RUN state: a reused engine must fire the hook on every run.
    eng = _eng(_graph(), _drv())
    fired = []
    hooks = {"screen:app/.A": [lambda ctx: fired.append(1) or cont()]}
    assert eng.run(hooks=hooks).status == "completed"
    assert eng.run(hooks=hooks).status == "completed"
    assert len(fired) == 2


def test_ambiguous_twin_never_keys_node_hooks():
    # two screens share a structure skeleton: keying injected code to sorted(cands)[0] was a
    # GUESS against a twin (review finding 10). Under ambiguity the node key must not fire and
    # ctx.node_id is None ("off-graph OR ambiguous — always None-check").
    g = _graph()
    g.upsert_screen(Screen(id="SX", namespace="app/.A", package="app", activity=".A",
                           structure_id=structure_id("app/.A", LAUNCH_XML)))  # S0's twin
    node_seen, fired = [], []
    out = _eng(g, _drv()).run(hooks={
        "screen:S0": [lambda ctx: fired.append(1) or cont()],
        "before:0": [lambda ctx: node_seen.append(ctx.node_id) or cont()],
    })
    assert out.status == "completed"
    assert fired == []           # never bound to a guessed twin
    assert node_seen == [None]   # honest: ambiguous -> None


def test_goto_suppresses_only_the_issuing_hook_at_the_resume_boundary():
    # post-goto suppression is scoped to the ISSUER (so it cannot immediately self-loop); an
    # INDEPENDENT hook at the same boundary still fires — the old blanket just_routed skip
    # silently dropped unrelated side-effect hooks at the goto target.
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    issued, other = [], []

    def issuer(ctx):
        if issued:
            return cont()
        issued.append(1)
        return goto("S1")

    out = eng.run(hooks={"before:1": [issuer, lambda ctx: other.append(1) or cont()]})
    assert out.status == "completed"
    assert len(issued) == 1   # fired once, then suppressed at the resume boundary
    assert len(other) == 1    # the independent hook DID fire at the resume boundary


def test_directive_consumes_the_arrival_screen_keys():
    # a non-cont directive earlier at the same boundary consumes the arrival: the tracker
    # advanced when the screen was observed, so the remaining screen keys of that visit never
    # fire (documented under-firing — the safe direction for side-effectful hooks).
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    grabbed = []
    out = eng.run(hooks={
        "before:0": [lambda ctx: goto("S1")],
        "screen:app/.A": [lambda ctx: grabbed.append(1) or cont()],
    })
    assert out.status == "completed"
    assert grabbed == []  # the goto consumed the only app/.A arrival


def test_goto_resumes_at_original_flow_indices():
    # index-keyed hooks always refer to the RECORDING's step numbers (review finding 14): after
    # a goto to S1, the command S2->S3 is still step 2 — an after:2 hook fires there. The old
    # rebased flow renumbered it to 1 and the hook silently mis-bound.
    g = _graph3()
    drv = FakeDriver(present_selectors=ALL3)
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    out = eng.run(hooks={
        "before:0": [lambda ctx: goto("S1")],
        "after:2": [lambda ctx: ctx.emit("at_original_step_2", True) or cont()],
    })
    assert out.status == "completed"
    assert out.data == {"at_original_step_2": True}
    assert ("text", "Continuar", "click") not in drv.taps  # branched past step 0
    assert ("text", "Next2", "click") in drv.taps           # step 1 ran
    assert ("text", "Next3", "click") in drv.taps           # step 2 ran


def test_goto_resume_index_zero_is_honored():
    # a goto back to the START resumes at index 0 — 0 is a real index, not a falsy "no resume"
    # (the isinstance contract on _do_goto's int-or-ReplayResult return).
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    once = []

    def back_once(ctx):
        if once:
            return cont()
        once.append(1)
        return goto("S0")

    out = eng.run(hooks={"before:1": [back_once]})
    assert out.status == "completed"
    assert drv.taps.count(("text", "Continuar", "click")) == 2  # step 0 replayed after the goto
    assert drv.taps.count(("text", "Next2", "click")) == 1


def test_goto_to_off_flow_target_is_typed_resume_off_flow():
    # the target HAS a recorded departure, but only into a region the flow drops (a launcher
    # return) — distinct from resume_empty (a true leaf). A goto promises a recorded
    # continuation, so this stops with its own typed label (adversarial must-document 3c).
    g = Graph()
    g.upsert_screen(Screen(id="A0", namespace="app/.A", package="app", activity=".A",
                           structure_id=structure_id("app/.A", LAUNCH_XML),
                           force_action=ForceAction("am_start", "app/.A", verified_fp="A0")))
    g.upsert_screen(Screen(id="A1", namespace="app/.B", package="app", activity=".B"))
    g.upsert_screen(Screen(id="L", namespace="com.sec.android.app.launcher/.Home",
                           package="com.sec.android.app.launcher", activity=".Home",
                           screen_type="homescreen"))
    g.add_transition(Transition(source="A0", target="A1", action=GO))
    g.add_transition(Transition(source="A1", target="L", action=NEXT2))  # dropped by the flow
    drv = _drv()
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", expected_id=to)
    out = eng.run(hooks={"after:0": [lambda ctx: goto("A1")]})
    assert out.status == "stopped"
    assert out.failed_step.error == "resume_off_flow:A1"


def test_graph_hooks_are_the_canonical_store():
    g = _graph()
    g.hooks["after:0"] = [lambda ctx: ctx.emit("from_graph", 1) or cont()]
    out = _eng(g, _drv()).run()  # no run-level hooks -> uses Graph.hooks
    assert out.status == "completed" and out.data == {"from_graph": 1}


def test_run_hooks_merge_over_graph_hooks():
    g = _graph()
    g.hooks["after:0"] = [lambda ctx: ctx.emit("graph", 1) or cont()]
    out = _eng(g, _drv()).run(hooks={"before:0": [lambda ctx: ctx.emit("run", 2) or cont()]})
    assert out.status == "completed" and out.data == {"graph": 1, "run": 2}


def test_run_hooks_concat_with_graph_hooks_on_the_same_key():
    # the merge must CONCAT per key — a dict-splat replace silently drops the graph's hooks.
    g = _graph()
    g.hooks["after:0"] = [lambda ctx: ctx.emit("from_graph", 1) or cont()]
    out = _eng(g, _drv()).run(hooks={"after:0": [lambda ctx: ctx.emit("from_run", 2) or cont()]})
    assert out.status == "completed"
    assert out.data == {"from_graph": 1, "from_run": 2}  # BOTH fired


def test_run_hook_directive_merges_over_graph_hook_on_the_same_key():
    # "merged OVER" = run-level precedence: at a shared key the run hook fires first, so when
    # both return a directive, the run-level one wins (first non-cont directive).
    g = _graph()
    g.hooks["before:0"] = [lambda ctx: stop("graph_decided")]
    out = _eng(g, _drv()).run(hooks={"before:0": [lambda ctx: stop("run_decided")]})
    assert out.status == "stopped" and out.failed_step.error == "hook_stop:run_decided"


def test_anchor_entered_by_edge_less_hop_replays_the_remaining_trace_honestly():
    # CONTRACT CHANGE (S23 ground truth): an edge-less hop off the anchor screen is a ROUTINE
    # recorded phenomenon — a chrome-forked reveal scroll (OEM collapsing toolbars fork both
    # identity tiers by default). The flow now starts right after the transition that ENTERED
    # the anchor and replays the remaining trace; honesty moves to the PER-STEP gates. The
    # original fear (a confident "completed" claiming the capture ran) stays impossible: the
    # remaining steps execute and verify, or stop typed at the exact step that cannot proceed.
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace="com.sec.android.app.launcher/.activities.LauncherActivity",
                           package="com.sec.android.app.launcher", activity=".activities.LauncherActivity",
                           screen_type="homescreen"))
    g.upsert_screen(Screen(id="S0", namespace="app/.A", package="app", activity=".A",
                           structure_id=structure_id("app/.A", LAUNCH_XML),
                           force_action=ForceAction("am_start", "app/.A", verified_fp="S0")))
    g.upsert_screen(Screen(id="S1", namespace="app/.B", package="app", activity=".B"))
    g.upsert_screen(Screen(id="S2", namespace="app/.C", package="app", activity=".C"))
    g.add_transition(Transition(source="L", target="S0", action=GO))      # S0 is only a TARGET
    g.add_transition(Transition(source="S1", target="S2", action=NEXT2))  # the post-hop step
    out = _eng(g, _drv()).run()
    # the post-hop step RAN and stopped honestly at ITS gate (Next2 never present) —
    # the capture is no longer wholesale-refused as flow_empty
    assert out.status == "stopped"
    assert out.failed_step.error.startswith("element not present")
    assert out.failed_step.selector_kind == "text"

    # and when the post-hop step's element IS reachable, the capture completes for real
    drv2 = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    out2 = _eng(g, drv2).run()
    assert out2.status == "completed"
    assert ("text", "Next2", "click") in drv2.taps


def test_flow_that_only_returns_home_stays_completed():
    # start IS on the trace; the only post-anchor step returns to the launcher, which the flow
    # drops BY DESIGN (a later launch supersedes it) -> a launch-only completed is honest.
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace="com.sec.android.app.launcher/.activities.LauncherActivity",
                           package="com.sec.android.app.launcher", activity=".activities.LauncherActivity",
                           screen_type="homescreen"))
    g.upsert_screen(Screen(id="S0", namespace="app/.A", package="app", activity=".A",
                           structure_id=structure_id("app/.A", LAUNCH_XML),
                           force_action=ForceAction("am_start", "app/.A", verified_fp="S0")))
    g.add_transition(Transition(source="L", target="S0", action=GO))
    g.add_transition(Transition(source="S0", target="L", action=GO))  # went home; flow drops it
    out = _eng(g, _drv()).run()
    assert out.status == "completed"


def test_no_hooks_is_unchanged_and_empty_data():
    out = _eng(_graph(), _drv()).run()
    assert out.status == "completed" and out.data == {}


def test_decorator_registration_via_hookregistry():
    # the idiomatic @ API: register with decorators, pass the registry to run().
    hooks = HookRegistry()
    seen = []

    @hooks.after(0)
    def grab(ctx):
        ctx.emit("v", 7)
        seen.append("after")
        return cont()

    @hooks.screen("app/.A")
    def at_screen(ctx):
        seen.append("screen")
        return cont()

    out = _eng(_graph(), _drv()).run(hooks=hooks)  # registry accepted just like a dict
    assert out.status == "completed" and out.data == {"v": 7}
    assert "after" in seen and "screen" in seen


def test_decorator_branch_stop_halts_honestly():
    hooks = HookRegistry()

    @hooks.before(0)
    def decide(ctx):
        return stop("declined")  # the branch: else -> honest stop

    out = _eng(_graph(), _drv()).run(hooks=hooks)
    assert out.status == "stopped" and out.failed_step.error == "hook_stop:declined"


def test_goto_reforce_reuses_the_launch_winning_rung_end_to_end():
    # E2E (real navigator, real shared ladder — no navigate() stub): the engine's cold launch
    # seeds the winning-rung cache (component refused -> package_default lands); a hook-goto's
    # re-force of the same anchor goes STRAIGHT to the proven rung. The refused component —
    # whose stop=True force-stop kills app state mid-replay — is issued exactly ONCE, ever.
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    drv.app_start_raises.add(("app", ".A"))  # the recorded component is non-exported
    eng = _eng(g, drv)
    # goto-time: the navigator first sees an unrecognized in-app screen (forcing the gated
    # recovery), and the app/.A namespace only after the recovery's app_start fires.
    eng._nav._observe = lambda: ((LAUNCH_XML, "app/.A", "app", True)
                                 if len(drv.app_starts) >= 3
                                 else (LAUNCH_XML, "app/.Deep", "app", True))
    fired = []

    def jump_back_once(ctx):
        if not fired:
            fired.append(True)
            return goto("S0")
        return cont()

    out = eng.run(hooks={"after:0": [jump_back_once]})
    assert out.status == "completed"
    # ONE refused component try (the launch), then package_default for the launch AND the
    # goto re-force — the cache prevented a second mid-replay force-stop thrash.
    assert drv.app_starts == [("app", ".A", True), ("app", None, True), ("app", None, True)]


def test_goto_navigation_failure_is_a_typed_stop_reason():
    # S23 finding: a hook goto whose inner navigate() refused (off_graph etc.) wrote the bare
    # nav status as the step error, which classify_stop cannot map -> stop_reason 'other'
    # (uninformative). The goto failure must carry a typed prefix AND the nav detail.
    from wendle.replay.result import StopReason
    g = _graph2()
    drv = FakeDriver(present_selectors={("text", "Continuar"), ("text", "Next2")})
    eng = _eng(g, drv)
    eng._nav.navigate = lambda frm, to: NavOutcome(
        "off_graph", expected_id=to, detail="fork-walk source not exact-verified")
    out = eng.run(hooks={"before:0": [lambda ctx: goto("S1")]})
    assert out.status == "stopped"
    assert out.stop_reason.kind == StopReason.GOTO_FAILED
    assert "off_graph" in (out.failed_step.error or "")
    assert "exact-verified" in (out.failed_step.error or "")  # the nav detail survives


def test_hook_stop_reason_scrubs_known_param_literals():
    # GAP #3 (completeness audit): redaction #4. A hook author who interpolates a credential param
    # VALUE into stop()'s reason must not leak it into ReplayResult.failed_step.error — the engine
    # scrubs known param values (replacing each with its ⟨handle⟩) before the reason lands in a
    # step. (Can't redact arbitrary developer text; this closes the cheap known-value leak.)
    drv = _drv()
    t = [0.0]
    eng = ReplayEngine(_graph(), drv, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                       lookup_timeout=5.0, retry_timeout=1.0, params={"password": "hunter2"})
    eng._observe = lambda: (LAUNCH_XML, "app/.A", "app", True)
    out = eng.run(hooks={"before:0": [lambda ctx: stop(f"leaked {ctx.params['password']}")]})
    assert out.status == "stopped"
    assert "hunter2" not in out.failed_step.error   # the secret value never lands in a step
    assert "password" in out.failed_step.error      # the value-free handle name survives


def test_param_value_colliding_with_a_label_token_never_corrupts_stop_classification():
    # Over-redaction guard (adversarial finding): scrubbing is scoped to the author's free-text
    # reason, NEVER the structural 'hook_stop:' prefix classify_stop keys on. A pathological param
    # value equal to a label fragment ('stop') must leave the TYPED classification intact.
    from wendle.replay.result import StopReason, classify_stop
    drv = _drv()
    t = [0.0]
    eng = ReplayEngine(_graph(), drv, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                       lookup_timeout=5.0, retry_timeout=1.0, params={"tok": "stop"})
    eng._observe = lambda: (LAUNCH_XML, "app/.A", "app", True)
    out = eng.run(hooks={"before:0": [lambda ctx: stop("ready")]})
    assert out.status == "stopped"
    assert out.failed_step.error.startswith("hook_stop:")  # structural prefix pristine
    assert classify_stop(out.failed_step.error).kind == StopReason.HOOK_STOP


def test_hook_failed_function_name_scrubs_known_param_literals():
    # review #4: a raising hook whose __name__ embeds a credential param value must not leak it
    # into failed_step.error — redaction #4 on the same surface the hook_stop fix hardened.
    drv = _drv()
    t = [0.0]
    eng = ReplayEngine(_graph(), drv, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                       lookup_timeout=5.0, retry_timeout=1.0, params={"password": "hunter2"})
    eng._observe = lambda: (LAUNCH_XML, "app/.A", "app", True)

    def check_hunter2(ctx):   # the function NAME embeds the secret
        raise RuntimeError("boom")

    out = eng.run(hooks={"before:0": [check_hunter2]})
    assert out.status == "stopped"
    assert "hunter2" not in out.failed_step.error
    assert out.failed_step.error.startswith("hook_failed:")
