"""Fork-twin routing (Cap 1) — the adversarially-corrected contract, executable.

The red tests here
encode the confident-wrong scenarios FIRST: the walk never originates corroboration,
never departs an unverified source, never replays a retreat, never lets a suspect waypoint
launder into confidence, and the scroll edge never drains a credential or poisons replay's
flow_empty guard into a confident 'completed'.
"""
import pytest

from wendle import reveal
from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, DeviceProfile, ForceAction, Screen, Selector, Transition
from wendle.record.session import RecordSession
from wendle.replay.commands import flow_from_recording
from wendle.replay.engine import ReplayEngine
from wendle.replay.result import StopReason

PKG = "com.app"
NS = f"{PKG}/.FeedActivity"
PROFILE = DeviceProfile(touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
                        display=(1080, 2340), touch_protocol="type_b")
NOSLEEP = {"sleep": lambda _dt: None}


# ---- chrome-fork frames (both identity tiers fork; the region survives) ----

def _chrome(big):
    if big:
        return (f'<node class="android.widget.TextView" package="{PKG}" resource-id="" '
                f'clickable="false" checkable="false" focusable="false" content-desc="" '
                f'text="Settings" scrollable="false" bounds="[40,80][1040,360]"/>'
                f'<node class="android.widget.EditText" package="{PKG}" '
                f'resource-id="{PKG}:id/search" clickable="true" checkable="false" '
                f'focusable="true" content-desc="" text="" scrollable="false" '
                f'bounds="[40,380][1040,500]"/>')
    return (f'<node class="android.widget.TextView" package="{PKG}" resource-id="" '
            f'clickable="false" checkable="false" focusable="false" content-desc="" '
            f'text="Settings" scrollable="false" bounds="[40,40][600,140]"/>')


def _forked(big, labels):
    rows = "".join(
        f'<node class="android.view.View" package="{PKG}" resource-id="" clickable="true" '
        f'checkable="false" focusable="false" content-desc="" text="" scrollable="false" '
        f'bounds="[0,{600 + i * 300}][1080,{900 + i * 300}]">'
        f'<node class="android.widget.TextView" package="{PKG}" resource-id="" clickable="false" '
        f'checkable="false" focusable="false" content-desc="" text="{lab}" scrollable="false" '
        f'bounds="[40,{610 + i * 300}][1000,{690 + i * 300}]"/></node>'
        for i, lab in enumerate(labels))
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">{_chrome(big)}'
        f'<node class="android.view.View" package="{PKG}" resource-id="" clickable="false" '
        f'checkable="false" focusable="false" content-desc="" text="" scrollable="true" '
        f'bounds="[0,600][1080,2100]">{rows}</node></node></hierarchy>'
    )


XML_TOP = _forked(True, ["Alpha", "Beta", "Gamma", "Delta"])
XML_SCR = _forked(False, ["Gamma", "Delta", "Batería", "Foxtrot"])
XML_DET = (f'<hierarchy><node class="android.widget.LinearLayout" package="{PKG}" '
           f'resource-id="{PKG}:id/detail" clickable="false" content-desc="" text="" '
           f'bounds="[0,0][1080,2340]"/></hierarchy>')
NS_DET = f"{PKG}/.DetailActivity"

T_TOP = fingerprint(NS, XML_TOP)
T_SCR = fingerprint(NS, XML_SCR)
DET = fingerprint(NS_DET, XML_DET)

SCROLL_ACTION = Action(selector=Selector("coords", (540, 1800)), action_type="swipe",
                       end=(540, 700), intent="reveal", in_region=True,
                       bounds=(0, 900, 1080, 1200), replayability="coordinate_only")


def _dumpsys(ns=NS):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def _session(*frames):
    hs, ds = [], []
    for xml in frames:
        hs += [xml] * 3
        ds += [_dumpsys()] * 3
    s = RecordSession(FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340)),
                      PROFILE, settle_kwargs=NOSLEEP, live_refresh=False)
    s.start()
    return s


def _swipe(y=1800, y2=700, x=540, x2=None):
    return Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=x, y=y, x2=x2 if x2 is not None else x,
                   y2=y2)


# ===== RECORDER: the scroll-class edge mint =====

def test_reveal_classified_fork_hop_mints_a_scroll_edge():
    s = _session(XML_TOP, XML_SCR)
    t = s.record_gesture(_swipe())
    assert t is not None and t.action_class == "scroll"
    assert t.action.intent == "reveal" and t.suspect_self_loop is False
    assert t.pre_actions == []  # NEVER drains pending (credential-drop guard)
    # the gesture ALSO still lands as intra evidence on the source (reveal eligibility)
    assert s.graph.screen(t.source).intra_actions[-1].intent == "reveal"
    # weight pinned swipe-like, never the coordinate_only 10x fall-through
    (_, _, _, data), = list(s.graph.ordered_transitions())
    assert data["weight"] <= 2.0
    # and the swipe is NOT in the routed-actions list
    assert all(a.action_type != "swipe" for a in s.graph.screen(t.source).actions)


def test_non_advance_fork_hop_mints_no_scroll_edge():
    # a retreat-sense hop is never a content-advance reveal, so it must NOT become a walkable
    # scroll edge — the navigator's reveal-walk can then never replay a pull-to-refresh (§3.4).
    # (A retreat ACROSS a real fork is an ordinary navigate edge, existing behavior; the point
    # here is only that NO scroll-class edge is minted.)
    s = _session(XML_TOP, XML_SCR)
    s.record_gesture(_swipe(y=700, y2=1800))  # retreat sense
    scroll_edges = [d for (_u, _v, _k, d) in s.graph.ordered_transitions()
                    if d.get("action_class") == "scroll"]
    assert scroll_edges == []


def test_scroll_edge_deduped_per_pair():
    # direct builder unit (the session-level choreography never re-forked the same pair, so it
    # exercised nothing — review finding: the dedup guard could be deleted and the suite stayed
    # green). Two mints for one (source, target) pair, different coordinates: exactly one edge.
    from wendle.record.builder import GraphBuilder
    b = GraphBuilder()
    first = b._commit_scroll_edge("U", "V", SCROLL_ACTION)
    again = b._commit_scroll_edge(
        "U", "V", Action(selector=Selector("coords", (300, 1900)), action_type="swipe",
                         end=(300, 800), intent="reveal", in_region=True))
    assert first is not None and again is None
    edges = [d for (_u, _v, _k, d) in b.graph.ordered_transitions()
             if d.get("action_class") == "scroll"]
    assert len(edges) == 1  # one scroll edge per (source, target), coordinates regardless
    assert b._has_scroll_edge("U", "V") and not b._has_scroll_edge("V", "U")


def test_fork_hop_never_drains_staged_credentials():
    s = _session(XML_TOP, XML_SCR, XML_DET.replace(NS_DET, NS))
    cred = Action(selector=Selector("resource_id", f"{PKG}:id/search"), action_type="set_text",
                  value={"param": "query"}, sensitive=True)
    s._builder.stage_pending(cred, s.current_id, NS)
    t = s.record_gesture(_swipe())
    assert t is not None and t.action_class == "scroll" and t.pre_actions == []
    assert len(s._pending) == 1  # the credential SURVIVED the hop to ride the next real edge


def test_sink_records_the_scroll_transition():
    events = []
    s = _session(XML_TOP, XML_SCR)
    s.sink = events.append
    s.record_gesture(_swipe())
    ev = [e for e in events if e.get("event_type") == "transition"]
    assert ev and ev[-1]["action_class"] == "scroll"


# ===== REPLAY: emission skip + flow_empty stays honest =====

def _fork_graph(with_tap=True, suspect_scrolled=False):
    g = Graph()
    g.upsert_screen(Screen(id=T_TOP, namespace=NS, package=PKG, activity=".FeedActivity",
                           structure_id=structure_id(NS, XML_TOP),
                           force_action=ForceAction("am_start", NS, verified_fp=T_TOP),
                           intra_actions=[SCROLL_ACTION]))
    g.upsert_screen(Screen(id=T_SCR, namespace=NS, package=PKG, activity=".FeedActivity",
                           structure_id=structure_id(NS, XML_SCR)))
    g.add_transition(Transition(source=T_TOP, target=T_SCR, action=SCROLL_ACTION,
                                action_class="scroll", settled=True))
    if suspect_scrolled:
        g.add_transition(Transition(source=T_SCR, target=T_SCR, suspect_self_loop=True,
                                    action=Action(selector=Selector("label", "x"),
                                                  action_type="click")))
    if with_tap:
        g.upsert_screen(Screen(id=DET, namespace=NS_DET, package=PKG, activity=".DetailActivity",
                               structure_id=structure_id(NS_DET, XML_DET)))
        g.add_transition(Transition(source=T_SCR, target=DET,
                                    action=Action(selector=Selector("label", "Batería"),
                                                  action_type="click", in_region=True,
                                                  bounds=(0, 1200, 1080, 1500))))
    return g


def test_flow_skips_scroll_edges_but_keeps_started_bookkeeping():
    flow = flow_from_recording(_fork_graph(), start_id=T_TOP)
    kinds = [(c.kind, c.action.action_type if c.action else None) for c in flow]
    assert kinds == [("action", "click")]  # the tap survives; the scroll never becomes a command


def test_anchor_departing_only_via_scroll_edges_is_flow_empty_not_completed():
    # the anchor (T_TOP) departs ONLY via a scroll edge; an earlier, unreachable non-scroll
    # step exists (skipped by not-started). Without excluding scroll from the engine's source
    # set, T_TOP would appear in `sources` via the scroll edge and the empty flow would return
    # a confident 'completed' — the cardinal sin. It must be a typed flow_empty.
    g = Graph()
    OTHER = "OTHER"
    g.upsert_screen(Screen(id=OTHER, namespace=NS, package=PKG, activity=".FeedActivity"))
    g.add_transition(Transition(source=OTHER, target=OTHER,  # chronologically FIRST, not T_TOP
                                action=Action(selector=Selector("label", "z"),
                                              action_type="click")))
    g.upsert_screen(Screen(id=T_TOP, namespace=NS, package=PKG, activity=".FeedActivity",
                           structure_id=structure_id(NS, XML_TOP),
                           force_action=ForceAction("am_start", NS, verified_fp=T_TOP)))
    g.upsert_screen(Screen(id=T_SCR, namespace=NS, package=PKG, activity=".FeedActivity",
                           structure_id=structure_id(NS, XML_SCR)))
    g.add_transition(Transition(source=T_TOP, target=T_SCR, action=SCROLL_ACTION,
                                action_class="scroll", settled=True))
    drv = FakeDriver()
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    eng._observe = lambda: (XML_TOP, NS, PKG, True)
    out = eng.run()
    assert out.status == "stopped"
    assert out.stop_reason.kind == StopReason.FLOW_EMPTY  # NEVER a confident 'completed'


# ===== NAVIGATOR: the walk, gated =====

def _nav(graph, frames_fn, present=()):
    from wendle.navigate.navigator import Navigator
    drv = FakeDriver(present_selectors=set(present))
    t = [0.0]
    nav = Navigator(graph, drv, clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    nav._observe = lambda: frames_fn(drv)
    return nav, drv


def _phase_obs(drv):
    # the in-region pre-route acts BOUNDS-ANCHORED (a coords tap from the settled dump),
    # so any tap — label OR coords — means the Bateria row was acted on
    if drv.taps:
        return (XML_DET, NS_DET, PKG, True)
    if drv.swipes:
        return (XML_SCR, NS, PKG, True)
    return (XML_TOP, NS, PKG, True)


def test_walk_bridges_the_fork_and_recorded_selector_edge_corroborates():
    nav, drv = _nav(_fork_graph(), _phase_obs, present={("text", "Batería")})
    out = nav.navigate(T_TOP, DET)
    assert out.status == "arrived"
    assert drv.swipes, "the scroll edge must be walked by swiping, not executed as an action"


def test_walk_itself_never_originates_corroboration(monkeypatch):
    # navigate TO the scrolled twin: the walk resolves it, but with recovery disabled the
    # arrival has no gate-earned trust — arrived_unverified, never a confident arrival.
    from wendle.navigate import navigator as nav_mod
    monkeypatch.setattr(nav_mod, "MAX_RESTARTS", 0)
    nav, drv = _nav(_fork_graph(with_tap=False), _phase_obs)
    out = nav.navigate(T_TOP, T_SCR)
    assert out.status != "arrived", "a coordinate scroll proves nothing — no corroboration"
    # CONTROL: the same hop as a recorded SELECTOR edge IS gate-earned trust
    g2 = _fork_graph(with_tap=False)
    # replace the scroll edge with a selector click edge
    g2.g.remove_edge(T_TOP, T_SCR)
    g2.add_transition(Transition(source=T_TOP, target=T_SCR,
                                 action=Action(selector=Selector("label", "Gamma"),
                                               action_type="click")))
    def obs2(drv):
        return (XML_SCR, NS, PKG, True) if drv.taps else (XML_TOP, NS, PKG, True)
    nav2, _ = _nav(g2, obs2, present={("text", "Gamma")})
    monkeypatch.setattr(nav_mod, "MAX_RESTARTS", 0)
    assert nav2.navigate(T_TOP, T_SCR).status == "arrived"


def test_walk_refuses_unverified_source():
    # L6 departure gate, pinned at the UNIT (review finding: the navigate()-level version
    # never reached the gate — actual=None stopped the loop earlier, so deleting the gate
    # left the suite green). Call _walk_scroll_edge directly with an observation that does
    # NOT reproduce the source id (the unrecorded-sibling / namespace-trust state): typed
    # off_graph stop, NO swipe ever issued.
    g = _fork_graph()
    nav, drv = _nav(g, _phase_obs, present={("text", "Batería")})
    (_, _, _, data), = [e for e in g.ordered_transitions()
                        if e[3].get("action_class") == "scroll"]
    # structurally foreign OUTSIDE the region (text changes and region content never enter
    # identity by design): a sibling whose chrome carries an extra tab-bar widget
    foreign = _forked(True, ["Alpha", "Beta", "Gamma", "Delta"]).replace(
        "</node></hierarchy>",
        f'<node class="android.widget.Button" package="{PKG}" resource-id="{PKG}:id/tabs" '
        f'clickable="true" checkable="false" focusable="false" content-desc="" text="" '
        f'scrollable="false" bounds="[0,2100][1080,2200]"/></node></hierarchy>')
    assert fingerprint(NS, foreign) not in (T_TOP, T_SCR)
    out = nav._walk_scroll_edge(T_TOP, T_SCR, data["action"], foreign, NS, PKG,
                                g.routable_subgraph(), 0, DET)
    assert out is not None and out.status == "off_graph"
    assert "not exact-verified" in (out.detail or "")
    assert drv.swipes == [], "never scroll a screen that does not reproduce the source id"
    # positive control: the SAME call with the exact source observation walks and resolves
    out2 = nav._walk_scroll_edge(T_TOP, T_SCR, data["action"], XML_TOP, NS, PKG,
                                 g.routable_subgraph(), 0, DET)
    assert out2 is None and drv.swipes, "exact source -> the walk runs and reaches the twin"


def test_suspect_waypoint_caps_downstream_confidence():
    nav, drv = _nav(_fork_graph(suspect_scrolled=True), _phase_obs,
                    present={("text", "Batería")})
    out = nav.navigate(T_TOP, DET)
    assert out.status != "arrived", "a §2.8-suspect waypoint must cap downstream confidence"


def test_walk_refuses_retreat_sense_against_live_region(monkeypatch):
    from wendle.navigate import navigator as nav_mod
    monkeypatch.setattr(nav_mod, "MAX_RESTARTS", 0)
    g = _fork_graph()
    # hand-corrupt the recorded gesture into a retreat (the recorder can no longer mint this,
    # but a hand-edited/legacy graph could carry it) — the walk must re-validate sense live
    (_, _, _, data), = [e for e in g.ordered_transitions() if e[3].get("action_class") == "scroll"]
    data["action"] = Action(selector=Selector("coords", (540, 700)), action_type="swipe",
                            end=(540, 1800), intent="reveal", in_region=True,
                            bounds=(0, 900, 1080, 1200))
    nav, drv = _nav(g, _phase_obs, present={("text", "Batería")})
    out = nav.navigate(T_TOP, DET)
    assert drv.swipes == [], "a retreat-sense gesture must never be issued (§3.4)"
    assert out.status != "arrived"


def test_walk_pre_resolves_without_swiping_when_already_there():
    # the 0-step branch, pinned at the UNIT (review finding: the navigate()-level version
    # planned from T_SCR directly so walk_to_node was never called — deleting the pre-resolve
    # branch left the suite green). The walk is entered with the source exact-verified but the
    # device ALREADY showing the target twin: it must resolve with ZERO gestures.
    g = _fork_graph()
    nav, drv = _nav(g, lambda d: (XML_SCR, NS, PKG, True), present={("text", "Batería")})
    (_, _, _, data), = [e for e in g.ordered_transitions()
                        if e[3].get("action_class") == "scroll"]
    # L6 gate sees the exact SOURCE frame; the walk's own first observation is the target
    out = nav._walk_scroll_edge(T_TOP, T_SCR, data["action"], XML_TOP, NS, PKG,
                                g.routable_subgraph(), 0, DET)
    assert out is None  # walk succeeded
    assert drv.swipes == [], "already on the target twin -> zero gestures issued"


def test_walk_stops_typed_on_a_recognized_foreign_screen(monkeypatch):
    from wendle.navigate import navigator as nav_mod
    monkeypatch.setattr(nav_mod, "MAX_RESTARTS", 0)
    g = _fork_graph()

    def obs(drv):
        if drv.swipes:
            return (XML_DET, NS_DET, PKG, True)  # strictly resolves to ANOTHER known node
        return (XML_TOP, NS, PKG, True)

    nav, drv = _nav(g, obs, present={("text", "Batería")})
    out = nav.navigate(T_TOP, DET)
    assert out.status != "arrived"
    assert len(drv.swipes) == 1, "stop the moment a NON-target known screen is recognized"


def test_walk_refuses_foreign_first_observation_with_zero_swipes():
    # a timed UI event (interstitial / auto-advance) can move the device between the L6
    # observation and the walk's own first observe — the walk must stop typed at 0 steps,
    # never issue even one mutating swipe against a screen it can already strictly
    # recognize as wrong (review finding: the foreign check only ran post-swipe).
    g = _fork_graph()
    nav, drv = _nav(g, lambda d: (XML_DET, NS_DET, PKG, True), present={("text", "Batería")})
    (_, _, _, data), = [e for e in g.ordered_transitions()
                        if e[3].get("action_class") == "scroll"]
    out = nav._walk_scroll_edge(T_TOP, T_SCR, data["action"], XML_TOP, NS, PKG,
                                g.routable_subgraph(), 0, DET)
    assert out is not None and out.reveal.reason == reveal.OFF_TARGET
    assert out.reveal.steps == 0 and drv.swipes == []


def test_scroll_only_capture_replays_launch_only_completed():
    # DELIBERATE semantics (mirrors test_flow_that_only_returns_home_stays_completed): a
    # capture whose ONLY edges are scroll hops recorded no replayable steps, so a launch-only
    # 'completed' is honest — nothing replayable was skipped. (The flow_empty stop is for
    # captures with OTHER replayable steps the flow cannot reach.)
    g = _fork_graph(with_tap=False)
    drv = FakeDriver()
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    eng._observe = lambda: (XML_TOP, NS, PKG, True)
    out = eng.run()
    assert out.status == "completed"
    assert all(s.kind != "action" for s in out.steps)  # launch only — no phantom steps


def test_goto_fork_top_resumes_via_scroll_closure():
    # Cap 1 review finding: a hook goto() to the fork's TOP twin used to stop resume_off_flow
    # even though a fresh replay from that very screen proceeds (the first command departs the
    # SCROLLED twin; the reveal rung bridges). The resume mapping now follows scroll edges.
    from wendle.navigate.navigator import NavOutcome
    from wendle.replay.hooks import goto
    g = _fork_graph()
    drv = FakeDriver(present_selectors={("text", "Batería")})
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    # show the SCROLLED viewport (where the in-region 'Bateria' row is actually visible —
    # the pre-route resolves region-bound and honestly refuses a frame without the row)
    eng._observe = lambda: (XML_SCR, NS, PKG, True)
    eng._nav.navigate = lambda frm, to: NavOutcome("arrived", tier="EXACT", expected_id=to)
    out = eng.run(hooks={"before:0": [lambda ctx: goto(T_TOP)]})
    assert out.status == "completed"
    assert any(s.kind == "goto" and s.ok for s in out.steps)
    # the post-fork tap ran — via the in-region pre-route, BOUNDS-ANCHORED from the
    # settled dump (a coords act at the matched row's center, the L5 discipline)
    assert drv.taps, "the post-fork in-region tap never ran"
