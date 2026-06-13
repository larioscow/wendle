"""The Maestro-cloned replay engine: run a recording's command list, per command
WAIT (poll) for the element → ACT → VERIFY → SETTLE, and STOP HONESTLY on any miss.

No blind sleeps: every wait polls a real condition with an injectable clock/sleep, so
device-free tests run at ~0 wall-time. Redaction-safe: typed values live only in `params`
and never reach a ReplayStep/error string.
"""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from wendle import reveal as _reveal
from wendle.actions import ActionContext, execute
from wendle.launch import LaunchLadder
from wendle.navigate.navigator import Navigator
from wendle.replay.commands import Command, flow_from_recording, launch_anchor
from wendle.replay.hooks import HookContext, cont
from wendle.replay.result import ReplayResult, ReplayStep

GOTO_BUDGET = 16  # hard cap on hook-driven gotos per run (mirrors the navigator's MAX_RESTARTS
#                   discipline): a re-entrant goto loop HALTS honestly instead of hanging.


class ReplayEngine:
    def __init__(
        self,
        graph,
        driver,
        *,
        params: Optional[Dict[str, str]] = None,
        settle_kwargs: Optional[dict] = None,
        lookup_timeout: float = 10.0,
        retry_timeout: float = 5.0,
        launch_timeout: float = 15.0,
        activity_launch_timeout: float = 4.0,
        verify_timeout: float = 3.0,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.graph = graph
        self.driver = driver
        self.params = params or {}
        self.lookup_timeout = lookup_timeout
        self.retry_timeout = retry_timeout
        self.launch_timeout = launch_timeout
        self.activity_launch_timeout = activity_launch_timeout
        self.verify_timeout = verify_timeout
        self.clock = clock
        self.sleep = sleep
        # ONE launch ladder behind ONE verify gate (replaces the old inline am_start chain).
        # `lambda: self._observe()` resolves at call time so a test that patches eng._observe
        # after construction is honored. The Navigator SHARES this ladder (one gate, one
        # winning-rung cache), so a hook-goto's re-force reuses the launch's proven rung
        # instead of re-thrashing refused rungs mid-replay.
        self._ladder = LaunchLadder(
            graph, driver, lambda: self._observe(), clock=clock, sleep=sleep,
            activity_launch_timeout=activity_launch_timeout, launch_timeout=launch_timeout,
        )
        self._nav = Navigator(graph, driver, params=self.params, settle_kwargs=settle_kwargs,
                              clock=clock, sleep=sleep, ladder=self._ladder)

    # observation/launch are delegated (tests may patch _observe to skip the settle machinery)
    def _observe(self):
        return self._nav._observe()

    @staticmethod
    def _waitable(selector) -> bool:
        return selector.kind not in ("coords", "keyevent")

    # ---- one recorded action -> (ok, error, low_confidence) via the shared ActionExecutor.
    #      faithful re-enactment policy: reproduce coordinate taps, type through the keyboard,
    #      verify the text landed. error strings are VALUE-FREE. ----
    def _act(self, action) -> Tuple[bool, Optional[str], bool]:
        ctx = ActionContext(self.driver, params=self.params, clock=self.clock, sleep=self.sleep,
                            verify_timeout=self.verify_timeout, reproduce_coords=True,
                            faithful_text=True, verify_text=True)
        r = execute(action, ctx)
        return r.ok, r.error, r.low_confidence

    def _step(self, index, cmd, ok, error=None, low_confidence=False, settled=True) -> ReplayStep:
        return ReplayStep(index=index, edge_index=cmd.edge_index, kind=cmd.kind,
                          action_type=cmd.action.action_type, selector_kind=cmd.action.selector.kind,
                          ok=ok, error=error, low_confidence=low_confidence, settled=settled)

    def _run_command(self, index: int, cmd: Command) -> ReplayStep:
        if cmd.kind == "launch":
            # a mid-flow app boundary: cold-launch the next app via the SAME ladder instead of
            # replaying the fragile launcher gesture that opened it (the multi-app finding).
            result = self._ladder.launch(cmd.anchor)
            return ReplayStep(index=index, edge_index=cmd.edge_index, kind="launch",
                              action_type="launch", selector_kind=cmd.anchor.kind,
                              ok=result.landed, error=result.error)
        action = cmd.action
        sel = action.selector
        t0 = self.clock()
        self._observe()  # inter-command idle gate (settle) — replaces a fixed sleep
        if action.in_region and action.action_type in ("click", "long_click"):
            # IN-REGION PRE-ROUTE (S23 decoy finding): the recorded element lived INSIDE the
            # adapter region — resolve it REGION-BOUND through the reveal rung (in-container
            # match, L5 check+act from one settled dump, scrolling as needed), never via the
            # global wait+act below, which can bind a chrome decoy carrying the same text
            # (rotating search-plate suggestions). NOT_ELIGIBLE falls through to the global path.
            report = self._attempt_reveal(cmd, action)
            if report.reason == _reveal.REVEALED:
                _x, _ns, _f, settled = self._observe()
                return self._step(index, cmd, True, None, False, settled)
            if report.reason != _reveal.NOT_ELIGIBLE:
                return self._step(index, cmd, False,
                                  f"{report.reason}: {sel.kind} after {report.steps} step(s)")
        if self._waitable(sel):
            budget = max(0.0, self.lookup_timeout - (self.clock() - t0))  # shared, never compounds
            if not self.driver.wait_until_present(sel, timeout=budget, clock=self.clock, sleep=self.sleep):
                # §3 reveal rung: a presence timeout on a reveal-eligible selector routes into
                # ONE bounded scroll-to-reveal attempt before the honest stop. The engine's
                # verified step boundary satisfies the rung's L6 source gate. On `revealed`
                # the bounds-anchored act already ran from the matching settled dump (L5).
                report = self._attempt_reveal(cmd, action)
                if report.reason == _reveal.REVEALED:
                    if report.acted:
                        # a tap-class action was performed inline, bounds-anchored (L5).
                        _x, _ns, _f, settled = self._observe()
                        return self._step(index, cmd, True, None, False, settled)
                    # a non-tap action (set_text / set_checked): the element is on screen now;
                    # run the RECORDED action against its real selector (never a bare tap).
                    ok, err, low = self._act(action)
                    if not ok:
                        return self._step(index, cmd, False, err or "did not resolve", low)
                    _x, _ns, _f, settled = self._observe()
                    return self._step(index, cmd, True, None, low, settled)
                if report.reason != _reveal.NOT_ELIGIBLE:
                    return self._step(index, cmd, False,
                                      f"{report.reason}: {sel.kind} after {report.steps} step(s)")
                return self._step(index, cmd, False, f"element not present: {sel.kind}")
        ok, err, low = self._act(action)
        if not ok and self._waitable(sel):
            self._observe()  # one bounded retry: the screen may still have been settling
            if self.driver.wait_until_present(sel, timeout=self.retry_timeout,
                                              clock=self.clock, sleep=self.sleep):
                ok, err, low = self._act(action)
        if not ok:
            return self._step(index, cmd, False, err or "did not resolve", low)
        _x, _ns, _f, settled = self._observe()
        return self._step(index, cmd, True, None, low, settled)

    def _attempt_reveal(self, cmd, action) -> "_reveal.RevealReport":
        """One bounded scroll-to-reveal attempt for this command's absent selector (§3.7).
        The source screen (whose recorded reveal gestures + region evidence gate
        eligibility) is the command's edge source in the chronological trace."""
        source_screen = None
        if cmd.edge_index >= 0:
            sources = [u for (u, _v, _k, _d) in self.graph.ordered_transitions()]
            if cmd.edge_index < len(sources):
                source_screen = self.graph.screen(sources[cmd.edge_index])
        return _reveal.attempt_reveal(self.driver, action, source_screen, self._observe,
                                      clock=self.clock, sleep=self.sleep)

    def _launch(self, anchor) -> Tuple[bool, Optional[str]]:
        """Cold-launch the app to the recorded screen via the shared LaunchLadder (recorded
        component -> recorded launcher icon tap -> package default), each behind the one
        verify_foreground honesty gate. A wrong surface is an honest force_failed, never a
        confident wrong-app replay."""
        result = self._ladder.launch(anchor)
        return result.landed, result.error

    def run(self, on_step=None, hooks=None) -> ReplayResult:
        """Replay the whole recording. `on_step(ReplayStep)` is called after each step (live
        progress). `hooks` (merged OVER the recording's own `Graph.hooks`) injects developer code at
        verified boundaries — keyed `before:<n>`/`after:<n>` (step) or `screen:<namespace>` — that
        runs in the gap and STEERS the replay (cont / stop honestly / [goto = increment 2])."""
        from wendle.graph import check_signature_version
        check_signature_version(self.graph)  # stale ids -> typed instant refusal (§2.6)

        def emit(step):
            if on_step is not None:
                on_step(step)
            return step

        # accept a HookRegistry (decorator API) or a raw {key: [hook]} dict, interchangeably;
        # both merge OVER the recording's own Graph.hooks (the canonical, never-serialized store):
        # per-key CONCAT — nothing is silently dropped — with the run-level hooks FIRST, so at a
        # shared key their directive takes precedence (first non-cont directive wins).
        raw = hooks.asdict() if hasattr(hooks, "asdict") else (hooks or {})
        registry = {key: [*raw.get(key, []), *self.graph.hooks.get(key, [])]
                    for key in {**self.graph.hooks, **raw}}
        data: dict = {}
        self._goto_count = 0  # per-run goto budget counter (see GOTO_BUDGET)
        # EDGE-TRIGGER tracker for screen-keyed hooks: the last TRUTHY observed namespace/node.
        # Reset per run (a reused engine must re-arm); a falsy observation NEVER resets it.
        self._last_ns: Optional[str] = None
        self._last_cur: Optional[str] = None
        steps = []
        anchor = launch_anchor(self.graph)
        start_id = None
        if anchor is not None:
            start_id = anchor.verified_fp
            ready, err = self._launch(anchor)  # try recorded activity -> fall back to package
            launch = emit(ReplayStep(0, -1, "launch", "launch", anchor.kind, ready, err))
            steps.append(launch)
            if not ready:
                return ReplayResult("stopped", steps, failed_step=launch, data=data)
        flow = flow_from_recording(self.graph, start_id=start_id)
        if not flow and start_id is not None:
            # the anchor screen never appears as a flow SOURCE (e.g. an implicit, edge-less hop
            # moved the recording off it), so NOTHING after launch can replay while the capture
            # HAS recorded steps: a typed stop (mirror of _do_goto's resume_empty), NEVER a
            # confident "completed" that claims the capture ran. SCROLL edges are excluded from
            # `sources` — they are skipped at emission (Cap 1) — so a start_id whose only
            # departures are scroll hops while OTHER replayable steps exist stops typed.
            # DELIBERATE: a capture whose ONLY edges are scroll hops (launch + exploration
            # scrolls, zero actions) yields an empty `sources` set and a launch-only
            # 'completed' — the same honest semantics as a flow that only returns home
            # (nothing replayable was recorded, so nothing replayable was skipped).
            sources = {u for (u, _v, _k, d) in self.graph.ordered_transitions()
                       if d.get("action_class") != "scroll"}
            if sources and start_id not in sources:
                fs = emit(ReplayStep(len(steps), -1, "flow", "flow", "-", False,
                                     f"flow_empty:{start_id}"))
                steps.append(fs)
                return ReplayResult("stopped", steps, failed_step=fs, data=data)
        i = 0
        suppress: tuple = ()  # the hook that issued the last goto — skipped exactly ONCE at the
        #                       resume boundary so it cannot immediately self-loop; every OTHER
        #                       hook (incl. independent ones keyed to the target) fires on the
        #                       verified arrival ("fires when that screen is reached").
        while i < len(flow):
            if registry:
                eff = self._boundary(registry, "before", i, steps, emit, data, flow, suppress)
                suppress = ()
                if eff is not None:
                    kind, val = eff
                    if kind == "return":
                        return val
                    i, suppress = val  # resume at the ORIGINAL-flow index the goto target owns
                    continue
            step = emit(self._run_command(len(steps), flow[i]))
            steps.append(step)
            if not step.ok:
                return ReplayResult("stopped", steps, failed_step=step, data=data)
            if registry:
                eff = self._boundary(registry, "after", i, steps, emit, data, flow, ())
                if eff is not None:
                    kind, val = eff
                    if kind == "return":
                        return val
                    i, suppress = val
                    continue
            i += 1
        return ReplayResult("completed", steps, data=data)

    def _boundary(self, registry, phase, i, steps, emit, data, flow, suppress):
        """Fire the hooks for this boundary and turn the result into an engine effect, or None.
        Returns ('return', ReplayResult) to terminate, or ('resume', (index, (issuer,))) after a
        goto — branched on isinstance, NEVER truthiness (index 0 = resume at the start is real)."""
        term, goto_node, issuer, cur = self._fire_phase(registry, phase, i, steps, emit, data, suppress)
        if term is not None:
            return ("return", term)
        if goto_node is not None:
            rerouted = self._do_goto(goto_node, cur, steps, emit, data, flow)
            if isinstance(rerouted, ReplayResult):
                return ("return", rerouted)
            return ("resume", (rerouted, (issuer,)))
        return None

    def _fire_phase(self, registry, phase, i, steps, emit, data, suppress=()):
        """Fire every hook whose key matches THIS boundary — the step index (`before:<i>`/`after:<i>`)
        always; the `screen:<namespace>` / `screen:<node_id>` keys EDGE-TRIGGERED (only on ARRIVAL:
        a truthy observation differing from the last truthy one — once per contiguous visit, re-armed
        by leaving and returning). Acts on the FIRST non-cont directive. Returns
        (terminal, goto_node, issuing_hook, current_node).

        Honesty rules (adversarially reviewed — see the hook-semantics-v2 plan):
        * A falsy observation (unsettled / ambiguous / off-graph moment) is NO SIGNAL: it never
          resets the tracker, so volatile churn V -> unresolved -> V cannot re-fire a side-effect.
        * `cur` comes from the STRICT resolver — None under twin ambiguity (never key injected
          code to a guessed node; same residual as the navigator for UNRECORDED twins).
        * The tracker advances from this boundary's observation BEFORE the key loop, so an early
          directive CONSUMES the arrival (remaining screen keys do not fire for that visit) —
          under-firing is the safe direction for side-effectful hooks.
        * Screen hooks fire only at replay boundaries: the entry screen at `before:0`, any other
          screen at the after-boundary of the command that reached it; screens traversed INSIDE a
          goto's navigation are not boundaries. An empty flow has no boundaries at all."""
        xml, ns, focus, _s = self._observe()
        cur = self._nav._actual_node(xml, ns, focus, self.graph.routable_subgraph(), strict=True)
        keys = [f"{phase}:{i}"]
        ns_s = str(ns) if ns else ""
        if ns_s and ns_s != self._last_ns:
            keys.append("screen:" + ns_s)
        if cur and cur != self._last_cur:  # precise keying (a 1-Activity app shares a namespace)
            keys.append("screen:" + cur)
        if ns_s:
            self._last_ns = ns_s
        if cur:
            self._last_cur = cur
        ctx = HookContext(driver=self.driver, node_id=cur, namespace=ns_s,
                          focus_pkg=focus, step_index=i, phase=phase, params=self.params,
                          graph=self.graph, _data=data)
        for key in keys:
            for hook in registry.get(key, []):
                if hook in suppress:
                    continue  # the goto issuer, skipped exactly once at its resume boundary
                name = getattr(hook, "__name__", "hook")
                try:
                    directive = hook(ctx) or cont()
                except Exception:  # noqa: BLE001 — arbitrary developer code; never barrel on
                    # scrub a credential value embedded in the function NAME (same surface/rule as
                    # the stop() reason); the structural 'hook_failed:' prefix stays outside the scrub
                    return (self._hook_stop(steps, emit, data,
                                            f"hook_failed:{self._redact_params(name)}"),
                            None, None, cur)
                if directive.kind == "cont":
                    continue
                if directive.kind == "stop":
                    # scrub credential param values from the AUTHOR'S reason only — the structural
                    # 'hook_stop:' prefix classify_stop keys on is built here and never scrubbed.
                    return (self._hook_stop(steps, emit, data,
                                            f"hook_stop:{self._redact_params(directive.reason)}"),
                            None, None, cur)
                if directive.kind == "goto":
                    return None, directive.node_id, hook, cur
        return None, None, None, cur

    def _do_goto(self, node_id, current_node, steps, emit, data, flow):
        """Branch to `node_id`: pathfind+verify there via the navigator, then RESUME the ORIGINAL
        flow at the first position whose command DEPARTS the target — so `before:<n>`/`after:<n>`
        hooks (and ctx.step_index) always mean the recording's own step numbers, never a rebased
        sub-flow. Returns an int resume index (0 = a goto back to the start, a real value — branch
        on isinstance, never truthiness) or an honest typed-terminal ReplayResult: the goto budget
        bounds ALL goto loops; an unreachable/unverified target maps the navigator's typed
        NavOutcome; a target with no in-flow continuation is `resume_empty` (a true leaf) or
        `resume_off_flow` (it departs only into a region the flow dropped — launcher-return /
        pre-anchor), NEVER a bare `completed`. A revisited target resumes at its FIRST recorded
        departure (chronological); skipped launch commands are safe — the navigator observes
        first and, whenever it must force, does so through the SHARED gated LaunchLadder, and
        its `arrived` here is tier-gated AND corroborated (a gated launch or a walked recorded
        edge) — never a zero-evidence claim."""
        self._goto_count += 1
        if self._goto_count > GOTO_BUDGET:
            return self._hook_stop(steps, emit, data, "goto_budget_exhausted", kind="goto")
        if node_id not in self.graph.g.nodes:
            return self._hook_stop(steps, emit, data, f"goto_no_route:{node_id}", kind="goto")
        outcome = self._nav.navigate(current_node, node_id)
        ok = outcome.status == "arrived"
        # a refused goto carries a TYPED prefix + the navigator's own status and (value-free)
        # detail, so callers see goto_failed:off_graph (fork-walk source not exact-verified)
        # instead of an unmapped bare status classifying as OTHER.
        err = outcome.status if ok else (
            f"goto_failed:{outcome.status}"
            + (f" ({outcome.detail})" if getattr(outcome, "detail", "") else ""))
        gstep = emit(ReplayStep(len(steps), -1, "goto", "goto", "-", ok, err))
        steps.append(gstep)
        if not ok:  # no_route / off_graph / arrived_unverified / cross_app... honest
            return ReplayResult("stopped", steps, failed_step=gstep, data=data)
        ordered = list(self.graph.ordered_transitions())
        sources = [u for (u, _v, _k, _d) in ordered]
        # Cap 1: a fork-source node departs only via a `scroll`-class edge the flow SKIPS —
        # but the flow's first command from the SCROLLED twin is still reachable from here
        # (the per-step reveal rung bridges the hop, exactly as a fresh run from this screen
        # would). Resume from any scroll-successor of the goto target, transitively (bounded:
        # the closure follows only scroll edges, which are deduped per pair).
        scroll_reachable = {node_id}
        grew = True
        while grew:
            grew = False
            for u, v, _k, d in ordered:
                if d.get("action_class") == "scroll" and u in scroll_reachable \
                        and v not in scroll_reachable:
                    scroll_reachable.add(v)
                    grew = True
        p = next((idx for idx, c in enumerate(flow)
                  if c.kind != "launch" and sources[c.edge_index] in scroll_reachable), None)
        if p is None:
            label = "resume_off_flow" if node_id in sources else "resume_empty"
            es = emit(ReplayStep(len(steps), -1, "goto", "goto", "-", False, f"{label}:{node_id}"))
            steps.append(es)
            return ReplayResult("stopped", steps, failed_step=es, data=data)
        return p

    def _hook_stop(self, steps, emit, data, reason, kind="hook") -> ReplayResult:
        hs = emit(ReplayStep(len(steps), -1, kind, kind, "-", False, reason))
        steps.append(hs)
        return ReplayResult("stopped", steps, failed_step=hs, data=data)

    def _redact_params(self, text) -> str:
        """Scrub known credential param VALUES out of the AUTHOR'S free-text stop() reason before
        it lands in a step (redaction #4). Scoped to the author text ONLY — the caller keeps the
        structural 'hook_stop:' prefix (which classify_stop keys on) OUT of here — so a short
        param value that over-matches can at worst garble the author's own label, NEVER the typed
        classification. Arbitrary developer text can't be redacted in general; this closes the
        known-value leak the framework actually owns. Each value -> its ⟨handle⟩."""
        out = text if isinstance(text, str) else ("" if text is None else str(text))
        for name, value in (self.params or {}).items():
            if isinstance(value, str) and value:
                out = out.replace(value, f"⟨{name}⟩")
        return out


def replay_recording(path_or_graph, driver, *, on_step=None, hooks=None, **kw) -> ReplayResult:
    """Convenience: load a recording (path or Graph) and replay it whole.

    `on_step`/`hooks` are forwarded to the run, so callers (the CLI included) can
    inject inter-step hooks without constructing a ReplayEngine themselves."""
    from wendle.graph import Graph

    graph = path_or_graph if isinstance(path_or_graph, Graph) else Graph.from_json(
        open(path_or_graph).read())
    return ReplayEngine(graph, driver, **kw).run(on_step=on_step, hooks=hooks)
