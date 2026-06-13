"""Regression tests for the max-effort review of the lazy-list change-set (10 CONFIRMED bugs).

Each test encodes the review's failure scenario and fails on the pre-fix code. Grouped by
the bug's owning layer. The honesty invariant is the throughline: none of these paths may
end in a confident-wrong action reported as success.
"""
import pytest

from wendle import reveal
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator
from wendle.replay.engine import ReplayEngine

NS = "com.app/.Feed"


# ---------- reveal-rung fixtures (mirror test_reveal_rung.py shapes) ----------

def _row(y, label, rid=""):
    return (
        f'<node class="android.view.View" resource-id="" clickable="true" checkable="false" '
        f'focusable="false" content-desc="" text="" scrollable="false" '
        f'bounds="[0,{y}][1080,{y + 300}]">'
        f'<node class="android.widget.{"EditText" if rid else "TextView"}" resource-id="{rid}" '
        f'clickable="{"true" if rid else "false"}" checkable="false" focusable="false" '
        f'content-desc="" text="{label}" scrollable="false" bounds="[40,{y + 10}][800,{y + 90}]"/>'
        "</node>"
    )


def _window(rows):
    body = "".join(rows)
    return (
        '<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
        'checkable="false" focusable="false" content-desc="" text="" scrollable="false" '
        'bounds="[0,0][1080,2340]">'
        '<node class="android.view.View" resource-id="" clickable="false" checkable="false" '
        'focusable="false" content-desc="" text="" scrollable="true" '
        f'bounds="[0,600][1080,2100]">{body}</node></node></hierarchy>'
    )


W1 = _window([_row(600 + i * 300, f"Item {i}") for i in range(4)])
W2 = _window([_row(600 + i * 300, lab) for i, lab in enumerate(["Item 9", "Item 8", "Target", "Item 7"])])
# a window whose Target row is an EditText bound by resource-id (for set_text reveal)
W2_FIELD = _window([_row(600, "x"), _row(900, "y"), _row(1200, "Target", rid="com.app:id/pwd"),
                    _row(1500, "z")])


def _obs(frames):
    seq = list(frames)
    return lambda: (seq.pop(0) if len(seq) > 1 else seq[0], NS, "com.app", True)


def _clock():
    t = [0.0]
    return (lambda: t[0]), t


def _action(action_type="click", kind="text", value="Target", in_region=True):
    return Action(selector=Selector(kind, value), action_type=action_type,
                  in_region=in_region, bounds=(0, 900, 1080, 1200))


# ===== Findings 1 & 3 & 7: the rung must not turn non-tap actions into a bare click =====

def test_reveal_taps_click_inline_and_marks_acted():
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action("click"), None, _obs([W1, W2]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.acted is True
    assert len(drv.taps) == 1 and drv.taps[0][0] == "coords" and drv.taps[0][2] == "click"


def test_reveal_long_click_acts_inline_preserving_action_type():
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action("long_click"), None, _obs([W1, W2]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.acted is True
    assert drv.taps[0][2] == "long_click"  # NOT silently degraded to a click


def test_reveal_set_text_reveals_without_acting():
    # the rung must NOT tap a set_text target — it reveals, marks acted=False, and the
    # CALLER runs the real set_text against the now-present selector (no silent skip).
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action("set_text", kind="resource_id", value="com.app:id/pwd"),
                                None, _obs([W1, W2_FIELD]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.acted is False
    assert drv.taps == [] and drv.text_sets == []  # nothing typed/tapped by the rung itself


class _RevealDriver(FakeDriver):
    """A field that is ABSENT until a reveal scroll happens (presence flips on first swipe) —
    models a below-the-fold element that wait_until_present can only reach after scrolling."""

    def wait_until_present(self, selector, timeout=10.0, **kw):
        if selector.kind in ("coords", "keyevent"):
            return True
        return bool(self.swipes) and self._key(selector) in self.present_selectors


def test_engine_runs_set_text_after_reveal_never_a_bare_tap():
    # the full engine path: a below-the-fold password field revealed, then ACTUALLY typed.
    fid = fingerprint(NS, W1)
    g = Graph()
    g.upsert_screen(Screen(id=fid, namespace=NS, package="com.app", activity=".Feed",
                           structure_id=structure_id(NS, W1),
                           force_action=ForceAction("am_start", NS, verified_fp=fid)))
    g.add_transition(Transition(source=fid, target=fid, action=_action(
        "set_text", kind="resource_id", value="com.app:id/pwd")))
    drv = _RevealDriver(present_selectors={("resource_id", "com.app:id/pwd")})
    t = [0.0]
    eng = ReplayEngine(g, drv, params={}, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    seq = [W1, W1, W1, W2_FIELD, W2_FIELD]
    eng._observe = lambda: (seq.pop(0) if len(seq) > 1 else seq[0], NS, "com.app", True)
    out = eng.run()
    assert out.status == "completed"
    assert drv.text_sets and any("com.app:id/pwd" in str(ts) for ts in drv.text_sets)
    assert drv.swipes, "the field was off-screen — the reveal rung must have scrolled"


# ===== Finding 8: deepest-match dedup inside the container (no false ambiguous) =====

def test_ancestor_and_leaf_same_label_resolve_uniquely_in_container():
    # a row container whose content-desc == its leaf TextView's text (TalkBack convention):
    # the driver applies pick_unique_deepest, so the rung must too — not refuse ambiguous.
    row = ('<node class="android.view.View" resource-id="" clickable="true" checkable="false" '
           'focusable="false" content-desc="Target" text="" scrollable="false" '
           'bounds="[0,1200][1080,1500]">'
           '<node class="android.widget.TextView" resource-id="" clickable="false" '
           'checkable="false" focusable="false" content-desc="" text="Target" scrollable="false" '
           'bounds="[40,1210][800,1290]"/></node>')
    w = _window([_row(600, "a"), _row(900, "b"), row, _row(1500, "d")])
    drv = FakeDriver()
    clock, _ = _clock()
    rep = reveal.attempt_reveal(drv, _action("click", kind="label"), None, _obs([w]), clock=clock)
    assert rep.reason == reveal.REVEALED and rep.acted is True  # unique deepest, not AMBIGUOUS


# ===== Findings 2/11: the gesture-zone clamp must never invert the swipe sense =====

def test_advance_swipe_never_inverts_for_a_flush_short_region():
    drv = FakeDriver(display=(1080, 2340))
    flush = {"bounds": (0, 2100, 1080, 2340), "axis": "y", "digests": [], "child_boxes": []}
    reveal._advance_swipe(drv, flush)
    (start, end), = drv.swipes
    assert start[1] > end[1], "a content-advance (up) swipe must start BELOW its end, never invert"
    # both endpoints clear the gesture zones
    assert end[1] >= int(2340 * reveal._EDGE_CLEAR)
    assert start[1] <= 2340 - int(2340 * reveal._EDGE_CLEAR)


def test_advance_swipe_horizontal_flush_does_not_invert():
    drv = FakeDriver(display=(1080, 2340))
    flush = {"bounds": (840, 600, 1080, 1000), "axis": "x", "digests": [], "child_boxes": []}
    reveal._advance_swipe(drv, flush)
    (start, end), = drv.swipes
    assert start[0] > end[0]  # advance = leftward; never inverted to a rightward back-gesture


# ===== Findings 4/9: recorded reveal-swipe classification needs axis dominance =====

from wendle.capture.types import Gesture
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
                        display=(1080, 2340), touch_protocol="type_b")
NOSLEEP = {"sleep": lambda _dt: None}
PKG = "com.app"
RNS = f"{PKG}/.FeedActivity"


def _feed_screen(labels):
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
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.view.View" package="{PKG}" resource-id="" clickable="false" '
        f'checkable="false" focusable="false" content-desc="" text="" scrollable="true" '
        f'bounds="[0,600][1080,2100]">{rows}</node></node></hierarchy>'
    )


def _dumpsys():
    return (f"topResumedActivity: ActivityRecord{{x u0 {PKG}/.FeedActivity t1}}",
            f"mCurrentFocus=Window{{x u0 {PKG}/.FeedActivity}}")


def _feed_session(*frames):
    hs, ds = [], []
    for xml in frames:
        hs += [xml] * 3
        ds += [_dumpsys()] * 3
    s = RecordSession(FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340)),
                      PROFILE, settle_kwargs=NOSLEEP, live_refresh=False)
    s.start()
    return s


def test_near_horizontal_row_swipe_is_probe_not_reveal():
    # a left-archive swipe with slight upward drift must NOT record as a replayable reveal
    rows1 = ["Alpha", "Beta", "Gamma", "Delta"]
    s = _feed_session(_feed_screen(rows1), _feed_screen(rows1))
    g = Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=1200, x2=90, y2=1196)  # dx=-450, dy=-4
    assert s.record_gesture(g) is None
    intra = s.graph.screen(s.current_id).intra_actions[-1]
    assert intra.intent == "probe", "a horizontal pan is not a content-advance reveal"


def test_vertical_advance_swipe_still_classifies_reveal():
    rows1 = ["Alpha", "Beta", "Gamma", "Delta"]
    s = _feed_session(_feed_screen(rows1), _feed_screen(rows1))
    g = Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=1800, x2=560, y2=700)  # mostly up
    assert s.record_gesture(g) is None
    assert s.graph.screen(s.current_id).intra_actions[-1].intent == "reveal"


# ===== Finding 5: _reconcile must refresh _settled_xml to the new screen's frame =====

def test_reconcile_updates_settled_xml_so_region_checks_use_the_new_screen():
    s = _feed_session(_feed_screen(["A", "B", "C", "D"]))
    # simulate a reconcile to a DIFFERENT screen via the fresh frame
    new_xml = _feed_screen(["X", "Y", "Z", "W"]).replace(".FeedActivity", ".OtherActivity")
    from wendle.capture.hierarchy import parse_hierarchy
    from wendle.capture.types import Snapshot
    from wendle.fingerprint.signature import structure_id as _sid
    other_ns = "com.app/.OtherActivity"
    s._fresh = {
        "ns": other_ns, "xml": new_xml, "cfg": None, "focus": "com.app",
        "snap": Snapshot(t_start=0.0, t_end=0.0, hierarchy_hash="h",
                         nodes=parse_hierarchy(new_xml, focus_pkg="com.app")),
        "id": "ZZ", "struct": _sid(other_ns, new_xml), "stable": 2, "profile_name": "view",
    }
    s.current_snapshot = None  # force the overlap guard to pass (no same-screen veto)
    s._reconcile_current_screen()
    assert s._settled_xml == new_xml, "region classification would otherwise read the old screen"


# ===== Finding 6: the §2.8 suspect cap must engage on a recorder-shaped graph =====

def test_suspect_self_loop_node_is_never_a_confident_arrival():
    # the recorder records a suspect edge as a SELF-LOOP (source==target). routable_subgraph
    # drops self-loops, so an edge-keyed guard is dead — the cap must key on the NODE.
    xml = ('<hierarchy><node class="androidx.compose.ui.platform.AndroidComposeView" '
           'resource-id="" clickable="false" content-desc=""><node class="android.view.View" '
           'resource-id="" clickable="false" content-desc="" text="Pick interests"/>'
           '</node></hierarchy>')
    ns = "app/.Wizard"
    sid = fingerprint(ns, xml)
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace="l/.Home", package="l",
                           force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(Screen(id=sid, namespace=ns, package="app", activity=".Wizard",
                           structure_id=structure_id(ns, xml), profile_name="compose",
                           value_bearing=True,
                           force_action=ForceAction("am_start", ns, verified_fp=sid)))
    # the recorder's real shape: a suspect SELF-LOOP on the node
    g.add_transition(Transition(source=sid, target=sid, suspect_self_loop=True,
                                action=Action(selector=Selector("text", "Next"), action_type="click")))
    assert g.has_suspect_self_loop(sid) is True
    t = [0.0]
    nav = Navigator(g, FakeDriver(), clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    nav._observe = lambda: (xml, ns, "app", True)
    out = nav.navigate(sid, sid)
    assert out.status != "arrived", "arrival at a known-ambiguous node must never be confident"


# ===== Finding (L6): reveal must require an EXACT-verified source, not bare namespace-trust =====

def test_reveal_gate_refuses_bare_namespace_trust():
    # corroborated-by-namespace-trust must NOT widen action reach into a scroll on an
    # unverified screen. Build: target edge whose source id is NOT reproduced by the live
    # screen (a same-namespace interstitial), action in-region — the rung must NOT fire.
    src_ns = "app/.A"
    real = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="app:id/real" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]"/></hierarchy>')
    other = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="app:id/promo" '
             'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]"/></hierarchy>')
    src_id = fingerprint(src_ns, real)
    tgt_id = fingerprint("app/.B", real)
    g = Graph()
    g.upsert_screen(Screen(id=src_id, namespace=src_ns, package="app", activity=".A",
                           structure_id=structure_id(src_ns, real),
                           force_action=ForceAction("am_start", src_ns, verified_fp=src_id)))
    g.upsert_screen(Screen(id=tgt_id, namespace="app/.B", package="app", activity=".B",
                           structure_id=structure_id("app/.B", real)))
    g.add_transition(Transition(source=src_id, target=tgt_id, action=_action(
        "click", kind="text", value="Continue")))
    drv = FakeDriver()  # the edge action never resolves
    t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    # the live screen is the OTHER interstitial (does NOT reproduce src_id) the whole run
    nav._observe = lambda: (other, src_ns, "app", True)
    nav.navigate(src_id, tgt_id)
    assert drv.swipes == [], "reveal must not scroll an unverified (namespace-only) screen"


# ===== Finding 10: label selectors keep the decayed-label leading-segment retry =====

def test_label_decay_retry_uses_stable_leading_segment():
    from wendle import actions
    # the row's current label (suffix moved); the exact recorded value no longer resolves,
    # but its stable leading segment "Alice" still does via the union contains path.
    drv = FakeDriver(present_selectors={("text", "Alice, sent 1 day ago")})
    ctx = actions.ActionContext(drv, reproduce_coords=True)
    act = Action(selector=Selector("label", "Alice, sent 5 min ago"), action_type="click")
    res = actions.execute(act, ctx)
    assert res.ok, "a label whose volatile suffix changed must retry on the stable prefix"
    assert any(v == "contains:Alice" for (_k, v, _a) in drv.taps)
