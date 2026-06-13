"""§3 — the scroll-to-reveal rung: bounded, typed, check+act from one settled dump.

Rung-level tests drive `attempt_reveal` with a controlled observe closure (full window
choreography, zero wall time); the integration tests run the REAL engine / navigator with
patched observation and injected clocks. The §2+§3 composition is load-bearing here: the
region collapse is exactly what makes two windows of one feed share an id, so replay's
arrival verification passes after a reveal scroll.
"""
import pytest

from wendle import reveal
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator
from wendle.replay.engine import ReplayEngine
from wendle.replay.result import StopReason

NS = "com.app/.Feed"


def _row(y, label, ticker=None):
    extra = ""
    if ticker is not None:
        extra = (f'<node class="android.widget.ProgressBar" resource-id="" clickable="false" '
                 f'checkable="false" focusable="false" content-desc="" text="{ticker}" '
                 f'scrollable="false" bounds="[900,{y + 10}][1060,{y + 60}]"/>')
    return (
        f'<node class="android.view.View" resource-id="" clickable="true" checkable="false" '
        f'focusable="false" content-desc="" text="" scrollable="false" '
        f'bounds="[0,{y}][1080,{y + 300}]">'
        f'<node class="android.widget.TextView" resource-id="" clickable="false" '
        f'checkable="false" focusable="false" content-desc="" text="{label}" scrollable="false" '
        f'bounds="[40,{y + 10}][800,{y + 90}]"/>{extra}</node>'
    )


def _window(labels, tickers=None, outside=""):
    rows = "".join(
        _row(600 + i * 300, lab, tickers[i] if tickers else None)
        for i, lab in enumerate(labels))
    return (
        '<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
        'checkable="false" focusable="false" content-desc="" text="" scrollable="false" '
        'bounds="[0,0][1080,2340]">' + outside +
        '<node class="android.view.View" resource-id="" clickable="false" checkable="false" '
        'focusable="false" content-desc="" text="" scrollable="true" '
        f'bounds="[0,600][1080,2100]">{rows}</node></node></hierarchy>'
    )


W1 = _window(["Alpha", "Beta", "Gamma", "Delta"])
W2 = _window(["Gamma", "Delta", "Target", "Epsilon"])


def _action(in_region=True, kind="text", value="Target"):
    return Action(selector=Selector(kind, value), action_type="click",
                  in_region=in_region, bounds=(0, 900, 1080, 1200))


def _screen(intra=()):
    return Screen(id="S", namespace=NS, intra_actions=list(intra))


def _obs(frames):
    seq = list(frames)
    return lambda: (seq.pop(0) if len(seq) > 1 else seq[0], NS, "com.app", True)


def _clock(t0=0.0):
    t = [t0]
    return (lambda: t[0]), t


def test_reveals_after_one_advance_step_and_acts_from_the_same_dump():
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs([W1, W2]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.steps == 1
    # one container-derived content-advance swipe (inset 10% of [600,2100], NOT screen-
    # center), with travel OVERLAP-CAPPED at half the container extent (S23 overshoot rule)
    (start, end), = drv.swipes
    assert start == (540, 1950)
    assert start[1] > end[1]  # advance sense
    assert start[1] - end[1] <= (2100 - 600) // 2  # consecutive viewports overlap
    # the act is a bounds-anchored coordinate tap at the matched node's center
    (kind, value, at), = drv.taps
    assert kind == "coords" and at == "click"
    assert value[1] == pytest.approx(1250, abs=60)  # the Target row's text center


def test_not_eligible_without_recorded_evidence():
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action(in_region=False), _screen(), _obs([W1]), clock=clock)
    assert rep.reason == reveal.NOT_ELIGIBLE and drv.swipes == [] and drv.taps == []


def test_coordinate_selectors_are_never_eligible():
    drv = FakeDriver()
    clock, _ = _clock()
    act = Action(selector=Selector("coords", (10, 10)), action_type="click", in_region=True)
    rep = reveal.attempt_reveal(drv, act, _screen(), _obs([W1]), clock=clock)
    assert rep.reason == reveal.NOT_ELIGIBLE


def test_no_container_when_no_region_observable():
    drv = FakeDriver()
    clock, _ = _clock()
    flat = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
            'content-desc="" text="" bounds="[0,0][1080,2340]"/></hierarchy>')
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs([flat]), clock=clock)
    assert rep.reason == reveal.NO_CONTAINER and drv.swipes == []


def test_two_in_container_matches_refuse_ambiguous():
    drv = FakeDriver()
    clock, _ = _clock()
    twins = _window(["Target", "Beta", "Target", "Delta"])
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs([twins]), clock=clock)
    assert rep.reason == reveal.AMBIGUOUS and drv.taps == []


def test_out_of_container_match_is_ignored():
    drv = FakeDriver()
    clock, _ = _clock()
    decoy = ('<node class="android.widget.TextView" resource-id="" clickable="false" '
             'checkable="false" focusable="false" content-desc="" text="Target" '
             'scrollable="false" bounds="[40,300][800,380]"/>')  # ABOVE the container
    w = _window(["Alpha", "Beta", "Gamma", "Delta"], outside=decoy)
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs([w, w]), clock=clock)
    assert rep.reason == reveal.NO_MOVEMENT  # the decoy never matched; window froze
    assert drv.taps == []


def test_frozen_window_stops_no_movement():
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs([W1, W1]), clock=clock)
    assert rep.reason == reveal.NO_MOVEMENT and rep.steps == 1 and drv.taps == []


def test_step_budget_terminates_endless_feeds():
    drv = FakeDriver()
    clock, _ = _clock()
    windows = [W1] + [_window([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]) for i in range(10)]
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs(windows), clock=clock,
                                max_steps=3)
    assert rep.reason == reveal.BUDGET and rep.bound == "steps" and rep.steps == 3


def test_wall_budget_fires_on_the_injected_clock():
    drv = FakeDriver()
    clock, t = _clock()
    windows = [W1] + [_window([f"A{i}", f"B{i}", f"C{i}", f"D{i}"]) for i in range(10)]

    def obs_and_advance(frames=_obs(windows)):
        t[0] += 9.0  # each observe cycle costs 9 fake seconds
        return frames()

    rep = reveal.attempt_reveal(drv, _action(), _screen(), obs_and_advance, clock=clock,
                                wall_budget=20.0)
    assert rep.reason == reveal.BUDGET and rep.bound == "wall"


def test_in_card_tickers_do_not_defeat_the_no_movement_signal():
    # §3.6: volatile-widget subtrees are stripped from the comparison — a per-row progress
    # ticker churns every dump, but the CONTENT is frozen, so no_movement still fires.
    drv = FakeDriver()
    clock, _ = _clock()
    wa = _window(["Alpha", "Beta", "Gamma", "Delta"], tickers=["1", "2", "3", "4"])
    wb = _window(["Alpha", "Beta", "Gamma", "Delta"], tickers=["9", "8", "7", "6"])
    rep = reveal.attempt_reveal(drv, _action(), _screen(), _obs([wa, wb]), clock=clock)
    assert rep.reason == reveal.NO_MOVEMENT


def test_label_selectors_are_reveal_eligible_and_union_matched():
    # S23 finding: the rung's matcher predated the §4 label kind — a freshly-recorded
    # label edge fell through as NOT_ELIGIBLE and replay stopped without ever scrolling.
    drv = FakeDriver()
    clock, _ = _clock()
    act = _action(kind="label", value="Target")
    rep = reveal.attempt_reveal(drv, act, _screen(), _obs([W1, W2]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.steps == 1
    # union semantics: a label must also match a node whose DESC carries the value
    w_desc = _window(["Alpha", "Beta", "Gamma", "Delta"]).replace(
        'content-desc="" text="Gamma"', 'content-desc="Target" text="Gamma"')
    drv2 = FakeDriver()
    rep2 = reveal.attempt_reveal(drv2, act, _screen(), _obs([w_desc]), clock=clock)
    assert rep2.reason == reveal.REVEALED and rep2.steps == 0


def test_recorded_reveal_gestures_replay_first_and_count_in_budget():
    drv = FakeDriver()
    clock, _ = _clock()
    recorded = Action(selector=Selector("coords", (540, 1700)), action_type="swipe",
                      intent="reveal", end=(540, 800))
    rep = reveal.attempt_reveal(drv, _action(), _screen(intra=[recorded]),
                                _obs([W1, W2]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.steps == 1
    (start, end), = drv.swipes
    assert start == (540, 1700) and end == (540, 800)  # faithfulness: the RECORDED gesture


# ---- engine integration: §2 collapse makes the post-reveal arrival verify pass ----

def _engine_case(frames):
    fid = fingerprint(NS, W1)
    g = Graph()
    g.upsert_screen(Screen(id=fid, namespace=NS, package="com.app", activity=".Feed",
                           structure_id=structure_id(NS, W1),
                           force_action=ForceAction("am_start", NS, verified_fp=fid)))
    g.add_transition(Transition(source=fid, target=fid, action=_action()))
    drv = FakeDriver(hierarchies=[W1], dumpsys_pairs=[("x", "x")])
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    seq = list(frames)
    eng._observe = lambda: (seq.pop(0) if len(seq) > 1 else seq[0], NS, "com.app", True)
    return eng, drv


def test_replay_reveals_and_completes():
    # the same feed at two scroll windows shares ONE id (the §2 collapse), so the engine's
    # arrival verification accepts the post-scroll screen — the disease and cure compose.
    assert fingerprint(NS, W1) == fingerprint(NS, W2)
    eng, drv = _engine_case([W1, W1, W1, W2, W2])
    out = eng.run()
    assert out.status == "completed"
    assert any(k == "coords" for (k, _v, _a) in drv.taps)  # the bounds-anchored act


def test_replay_stops_typed_when_reveal_finds_nothing():
    eng, drv = _engine_case([W1, W1, W1, W1])  # the window never moves
    out = eng.run()
    assert out.status == "stopped"
    assert out.stop_reason.kind == StopReason.REVEAL_NO_MOVEMENT


# ---- navigator integration: gate + typed report on NavOutcome.reveal ----

NS_T = "com.app/.Detail"
T_XML = ('<hierarchy><node class="android.widget.LinearLayout" resource-id="com.app:id/detail" '
         'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]"/></hierarchy>')


def _nav_case(frames):
    h_id = fingerprint(NS, W1)
    t_id = fingerprint(NS_T, T_XML)
    g = Graph()
    g.upsert_screen(Screen(id=h_id, namespace=NS, package="com.app", activity=".Feed",
                           structure_id=structure_id(NS, W1),
                           force_action=ForceAction("am_start", NS, verified_fp=h_id)))
    g.upsert_screen(Screen(id=t_id, namespace=NS_T, package="com.app", activity=".Detail",
                           structure_id=structure_id(NS_T, T_XML)))
    g.add_transition(Transition(source=h_id, target=t_id, action=_action()))
    drv = FakeDriver()
    t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    seq = list(frames)
    nav._observe = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
    return nav, drv, h_id, t_id


def test_navigator_reveals_then_arrives():
    h = (W1, NS, "com.app", True)
    h2 = (W2, NS, "com.app", True)
    tt = (T_XML, NS_T, "com.app", True)
    nav, drv, h_id, t_id = _nav_case([h, h, h2, tt])
    out = nav.navigate(h_id, t_id)
    assert out.status == "arrived"
    assert any(k == "coords" for (k, _v, _a) in drv.taps)


def test_navigator_reports_typed_reveal_refusal_as_content_drift():
    h = (W1, NS, "com.app", True)
    nav, drv, h_id, t_id = _nav_case([h, h, h, h])  # frozen window
    out = nav.navigate(h_id, t_id)
    assert out.status == "content_drift"
    assert out.reveal is not None and out.reveal.reason == reveal.NO_MOVEMENT
    assert "reveal_no_movement" in out.detail


# ---- flow building across a reveal gap (S23 finding) ----

def test_flow_starts_after_anchor_entry_despite_edge_less_fork_hop():
    # On-device: a chrome-forked reveal scroll moves the trace from the anchor screen to a
    # forked node WITHOUT an edge, so the anchor never appears as a flow SOURCE. The flow
    # must start right after the transition that ENTERED the anchor screen (the launch
    # supersedes that edge), not refuse the whole replay as flow_empty.
    from wendle.replay.commands import flow_from_recording

    g = Graph()
    g.upsert_screen(Screen(id="L", namespace="com.l.launcher/.Home", package="com.l.launcher",
                           force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(Screen(id="A", namespace=NS, package="com.app", activity=".Feed",
                           force_action=ForceAction("am_start", NS, verified_fp="A"),
                           intra_actions=[Action(selector=Selector("coords", (540, 1800)),
                                                 action_type="swipe", intent="reveal",
                                                 end=(540, 600))]))
    g.upsert_screen(Screen(id="A2", namespace=NS, package="com.app", activity=".Feed"))
    g.upsert_screen(Screen(id="B", namespace=NS_T, package="com.app", activity=".Detail"))
    g.add_transition(Transition(source="L", target="A", action=Action(
        selector=Selector("content_desc", "App"), action_type="click")))
    g.add_transition(Transition(source="A2", target="B", action=_action()))  # the fork gap
    flow = flow_from_recording(g, start_id="A")
    kinds = [(c.kind, c.action.selector.kind if c.action else None) for c in flow]
    assert kinds == [("action", "text")]  # the post-fork edge replays; the entry edge is dropped


# ---- swipe geometry: system gesture zones (S23 finding) ----

def test_advance_swipe_clears_system_gesture_zone_at_screen_edges():
    # On-device finding: a container flush with the screen's bottom edge put the 10%-inset
    # swipe start inside the gesture-nav zone — the OS swallowed it and the ineffective
    # gesture read as no_movement. Rule: when the container shares an edge with the screen,
    # the inset grows to a screen-fraction clearance.
    drv = FakeDriver(display=(1440, 3088))
    flush = {"bounds": (0, 1229, 1440, 3088), "axis": "y", "digests": [], "child_boxes": []}
    reveal._advance_swipe(drv, flush)
    (start, end), = drv.swipes
    assert start[1] <= 3088 - int(3088 * reveal._EDGE_CLEAR)  # clear of the gesture zone
    assert end[1] >= start[1] - (3088 - 1229)  # still a content-advance up-swipe
    assert end[1] < start[1]

    drv2 = FakeDriver(display=(1440, 3088))
    inner = {"bounds": (0, 600, 1440, 2100), "axis": "y", "digests": [], "child_boxes": []}
    reveal._advance_swipe(drv2, inner)
    (s2, e2), = drv2.swipes
    assert s2[1] == 2100 - max(1, int(1500 * 0.10))  # plain container inset, untouched


# ---- §4: in-region coordinates refuse UNCONDITIONALLY at the shared executor ----

def test_in_region_coordinate_actions_refuse_even_in_replay():
    from wendle import actions

    drv = FakeDriver()
    ctx = actions.ActionContext(drv, reproduce_coords=True)  # replay policy
    tap = Action(selector=Selector("coords", (540, 900)), action_type="click", in_region=True)
    res = actions.execute(tap, ctx)
    assert not res.ok and res.reason == actions.COORDINATE_REFUSED
    swipe = Action(selector=Selector("coords", (540, 900)), action_type="swipe",
                   end=(540, 300), in_region=True)
    res2 = actions.execute(swipe, ctx)
    assert not res2.ok and res2.reason == actions.COORDINATE_REFUSED
    assert drv.taps == [] and drv.swipes == []  # nothing was issued
    # control: an out-of-region coordinate tap keeps replay's reproduce semantics
    out_tap = Action(selector=Selector("coords", (540, 900)), action_type="click")
    assert actions.execute(out_tap, ctx).ok


def test_in_container_match_is_dom_subtree_not_geometry():
    # S23 finding (Samsung Settings floating search pill): an overlay can sit GEOMETRICALLY
    # inside the list container's bounding box while belonging to a SIBLING DOM branch
    # (coordinator/floating_bottom_container). Its rotating text periodically equals a real
    # row's label — bounds-containment matching then taps the pill (SearchActivity) instead
    # of scrolling to the real row. In-container means DOM-SUBTREE membership.
    from wendle.reveal import _matches_in_container
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
           # the list container: rows are DOM children
           '<node class="androidx.recyclerview.widget.RecyclerView" '
           'resource-id="app:id/recycler_view" bounds="[0,300][1440,3032]">'
           '<node class="android.widget.TextView" text="Sound" bounds="[270,400][1041,500]"/>'
           '</node>'
           # the floating pill: geometrically INSIDE the recycler bbox, DOM-OUTSIDE it,
           # currently rotating the decoy text
           '<node class="android.widget.LinearLayout" resource-id="app:id/floating_bottom" '
           'bounds="[0,2799][1440,3032]">'
           '<node class="android.widget.TextView" text="Lock screen" '
           'bounds="[262,2820][1177,2980]"/></node>'
           '</node></hierarchy>')
    found = _matches_in_container(xml, "label", "Lock screen", (0, 300, 1440, 3032))
    assert found == [], "a DOM-outside overlay must never match in-container"
    found2 = _matches_in_container(xml, "label", "Sound", (0, 300, 1440, 3032))
    assert len(found2) == 1  # the real DOM-child row still matches


def test_occluded_match_is_skipped_so_the_rung_scrolls_it_clear():
    # S23 z-order finding: the real row CAN be a DOM child of the region while POSITIONED
    # under a floating overlay (Samsung's bottom search pill). The dump can't say z-order,
    # but Android draws later document-order branches ON TOP — a tap at the row's center is
    # swallowed by the pill (SearchActivity). A match whose tap point is occluded by a
    # later-drawn foreign branch must NOT be acted on; the rung keeps scrolling until the
    # row surfaces clear of the overlay.
    from wendle.reveal import _matches_in_container
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
           '<node class="androidx.recyclerview.widget.RecyclerView" '
           'resource-id="app:id/recycler_view" bounds="[0,300][1440,3032]">'
           # this row sits INSIDE the overlay band — its center (655,2890) is covered
           '<node class="android.widget.TextView" text="Lock screen" '
           'bounds="[270,2840][1041,2940]"/>'
           # this one is clear
           '<node class="android.widget.TextView" text="Sound" bounds="[270,400][1041,500]"/>'
           '</node>'
           # the floating pill branch draws LATER (on top)
           '<node class="android.widget.LinearLayout" resource-id="app:id/floating_bottom" '
           'clickable="true" bounds="[0,2799][1440,3032]">'
           '<node class="android.widget.TextView" text="Buscar" '
           'bounds="[262,2820][1177,2980]"/></node>'
           '</node></hierarchy>')
    assert _matches_in_container(xml, "label", "Lock screen", (0, 300, 1440, 3032)) == [], \
        "an occluded row must not be tapped — scroll it clear instead"
    assert len(_matches_in_container(xml, "label", "Sound", (0, 300, 1440, 3032))) == 1


def test_advance_span_caps_travel_at_half_the_container():
    # S23 overshoot finding: a full-viewport advance (plus fling) can JUMP OVER the target
    # row's only clear positions — and advance-only scrolling cannot recover (§3.4 forbids
    # retreat). Consecutive viewports must OVERLAP: travel is capped at HALF the container
    # extent, so any element visible between two stops appears in at least one of them.
    from wendle.reveal import _advance_span
    lo, hi = 300, 3032  # the Settings recycler band
    start, end = _advance_span(lo, hi, 3120)
    assert start > end  # advance sense preserved
    assert (start - end) <= (hi - lo) // 2, f"travel {start-end} exceeds half-extent overlap"
    # degenerate short regions still behave (no inversion)
    s2, e2 = _advance_span(1000, 1200, 3120)
    assert s2 >= e2


def test_advance_swipe_starts_clear_of_a_bottom_overlay():
    # S23 root cause of reveal_no_movement: the list region extends UNDER a floating bottom
    # pill (search bar). A content-advance swipe whose START lands on the pill is swallowed —
    # the list never scrolls, so the rung wrongly reports no_movement. The swipe must
    # originate from the container band CLEAR of any later-drawn foreign overlay.
    from wendle.reveal import _clear_advance_band
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
           '<node class="androidx.recyclerview.widget.RecyclerView" '
           'resource-id="app:id/recycler_view" bounds="[0,1229][1440,3088]">'
           '<node class="android.widget.TextView" text="Row" bounds="[270,1300][1041,1400]"/>'
           '</node>'
           '<node class="android.widget.LinearLayout" resource-id="app:id/floating_bottom" '
           'bounds="[0,2799][1440,3088]">'
           '<node class="android.widget.TextView" text="Search" bounds="[262,2820][1177,2980]"/>'
           '</node></node></hierarchy>')
    # the container is (0,1229,1440,3088); the pill occludes its lower [2799,3088]
    lo, hi = _clear_advance_band(xml, (0, 1229, 1440, 3088), axis="y")
    assert hi <= 2799, f"advance band must stop above the pill, got hi={hi}"
    assert lo == 1229
    # no FOREIGN overlay (the pill is a genuine DOM child of the list, e.g. a sticky footer
    # row) -> the full band; only later-drawn SIBLING overlays shrink it
    xml2 = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
            '<node class="androidx.recyclerview.widget.RecyclerView" '
            'resource-id="app:id/recycler_view" bounds="[0,1229][1440,3088]">'
            '<node class="android.widget.TextView" text="Row" bounds="[270,1300][1041,1400]"/>'
            '<node class="android.widget.LinearLayout" bounds="[0,2799][1440,3088]">'
            '<node class="android.widget.TextView" text="Footer" bounds="[262,2820][1177,2980]"/>'
            '</node></node></node></hierarchy>')
    lo2, hi2 = _clear_advance_band(xml2, (0, 1229, 1440, 3088), axis="y")
    assert (lo2, hi2) == (1229, 3088)


def test_partially_occluded_match_acts_on_its_clear_subregion():
    # S23: a row whose CENTER is under the floating pill but whose TOP half is clear must
    # still be actable — tap the clear sub-region's center, not skip-and-overscroll. Only a
    # FULLY covered match is skipped.
    from wendle.reveal import _matches_in_container
    # row spans [1300,1500]; pill covers from y=1400 down -> top half [1300,1400] is clear
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
           '<node class="androidx.recyclerview.widget.RecyclerView" '
           'resource-id="app:id/list" bounds="[0,300][1440,1600]">'
           '<node class="android.widget.TextView" text="Lock" bounds="[270,1300][1041,1500]"/>'
           '</node>'
           '<node class="android.widget.LinearLayout" resource-id="app:id/pill" '
           'clickable="true" bounds="[0,1400][1440,1600]">'
           '<node class="android.widget.TextView" text="x" bounds="[262,1420][1177,1560]"/>'
           '</node></node></hierarchy>')
    found = _matches_in_container(xml, "label", "Lock", (0, 300, 1440, 1600))
    assert len(found) == 1
    box = found[0]  # bounds of the CLEAR sub-region
    cy = (box[1] + box[3]) // 2
    assert box[3] <= 1400, "the acted box must be the CLEAR sub-region (above the pill)"
    assert 1300 <= cy < 1400  # tap lands in the visible top half

    # fully covered -> skipped (the rung scrolls it clear)
    xml_full = xml.replace('bounds="[0,1400][1440,1600]"', 'bounds="[0,1280][1440,1600]"')
    assert _matches_in_container(xml_full, "label", "Lock", (0, 300, 1440, 1600)) == []


def test_advance_swipe_is_fling_free_slow_drag():
    # S23 overshoot ROOT CAUSE: a fast swipe flings the list FAR past the target's viewport,
    # so the rung scrolls right past the row (row present -> absent in one step) and ends
    # no_movement at the list bottom. A generated advance must be a CONTROLLED, fling-free
    # drag — duration scaled to distance (a bounded px/s), so each step advances ~one viewport.
    from wendle.reveal import _advance_swipe
    from wendle.driver.fake import FakeDriver

    class TimedDriver(FakeDriver):
        def __init__(self):
            super().__init__(display=(1440, 3120))
            self.durations = []

        def swipe(self, start, end, duration=0.2):
            self.durations.append((abs(start[1] - end[1]), duration))

        def display_size(self):
            return (1440, 3120)

    drv = TimedDriver()
    region = {"bounds": (0, 300, 1440, 3000), "axis": "y", "digests": [], "child_boxes": []}
    _advance_swipe(drv, region)
    dist, dur = drv.durations[-1]
    # fling-free: at most ~900 px/s (UiScrollable-style controlled scroll)
    assert dur >= dist / 900.0, f"swipe too fast ({dist}px in {dur}s) — will fling past target"


def test_container_subtree_found_when_region_bounds_differ_from_node_bounds():
    # S23 regression the DOM-subtree change introduced: region_geometry reports the region as
    # the CONTENT/clipped box (0,334,...) while the scrollable NODE's literal bounds are the
    # full screen (0,0,1440,3088). Exact-bounds container lookup then found nothing -> zero
    # matches -> the rung scrolled past a PRESENT row. The container is the smallest node that
    # CONTAINS the region bounds; its subtree still excludes the sibling pill.
    from wendle.reveal import _matches_in_container
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
           '<node class="androidx.recyclerview.widget.RecyclerView" '
           'resource-id="app:id/list" bounds="[0,0][1440,3088]">'
           '<node class="android.widget.TextView" text="Lock" bounds="[270,400][1041,500]"/>'
           '</node>'
           '<node class="android.widget.LinearLayout" resource-id="app:id/pill" '
           'bounds="[0,2799][1440,3088]">'
           '<node class="android.widget.TextView" text="Lock" bounds="[262,2820][1177,2980]"/>'
           '</node></node></hierarchy>')
    # the BOUND region (from region_geometry) is the clipped content box, NOT the node bounds
    found = _matches_in_container(xml, "label", "Lock", (0, 334, 1440, 3088))
    assert len(found) == 1, "the present row must match despite region!=node bounds"
    assert found[0][1] < 1000  # the real row (top), not the pill decoy


def test_nonclickable_decoration_overlay_does_not_occlude():
    # S23 round-corner finding: OEM skins draw a full-screen non-clickable decorative View
    # (rounded corners / scrim / ripple) ON TOP of everything. It is TOUCH-TRANSPARENT —
    # taps fall through to the node beneath — so it must NOT count as an occluder, else every
    # row reads occluded and the rung skips them all. Only a CLICKABLE later-drawn node (which
    # would actually consume the tap, e.g. a floating search box) occludes.
    from wendle.reveal import _matches_in_container
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1440,3120]">'
           '<node class="androidx.recyclerview.widget.RecyclerView" '
           'resource-id="app:id/list" bounds="[0,0][1440,3088]">'
           '<node class="android.widget.TextView" text="Lock" bounds="[270,500][1041,600]"/>'
           '</node>'
           # full-screen non-clickable decoration drawn LAST — must be ignored
           '<node class="android.view.View" resource-id="app:id/round_corner" '
           'clickable="false" bounds="[0,0][1440,3120]"/>'
           '</node></hierarchy>')
    found = _matches_in_container(xml, "label", "Lock", (0, 0, 1440, 3088))
    assert len(found) == 1, "a non-clickable decoration must not occlude the row"
    # a CLICKABLE overlay at the same spot DOES occlude (real touch consumer)
    xml2 = xml.replace('resource-id="app:id/round_corner" clickable="false"',
                       'resource-id="app:id/cover" clickable="true"')
    assert _matches_in_container(xml2, "label", "Lock", (0, 0, 1440, 3088)) == []
