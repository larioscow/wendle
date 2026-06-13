"""Tap-time binding + dropped-navigation reconciliation.

Two on-device findings:
  * a tap must bind to the layout on screen AT TAP TIME, not the stale arrival snapshot
    (Samsung Settings collapsing toolbar moved the list between arrival and tap);
  * when a navigating tap is DROPPED (fast multi-tab apps like Instagram), the recorder
    must attribute the next tap to the screen it actually happened on, not fabricate a
    bogus direct edge from a stale node.
"""
from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.types import Gesture, Snapshot
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.compose import resolve_profile
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession, _profile_name

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
NOSLEEP = {"sleep": lambda _dt: None}
NS = "com.app/.AActivity"


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def _frame(ns_pkg, *children):
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{ns_pkg}" resource-id="" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'{"".join(children)}</node></hierarchy>'
    )


def _btn(ns_pkg, rid, text, top=500, bottom=620):
    return (
        f'<node class="android.widget.Button" package="{ns_pkg}" resource-id="{ns_pkg}:id/{rid}" '
        f'clickable="true" content-desc="" text="{text}" bounds="[40,{top}][1040,{bottom}]"/>'
    )


def _fresh_dict(xml, ns, stable=2):
    cfg = resolve_profile(xml, ns)
    return {
        "ns": ns, "snap": Snapshot(t_start=0.0, t_end=0.0, hierarchy_hash="", nodes=parse_hierarchy(xml)),
        "id": fingerprint(ns, xml, cfg), "struct": structure_id(ns, xml),
        "profile_name": _profile_name(cfg, ns), "focus": None, "stable": stable,
    }


def _tap(y=560):
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=y)


# ---- tap-time binding: same screen, layout shifted between arrival and tap ----

# arrival: "go" sits at the tap point. tap-time (after a collapse): "more" sits there, but
# BOTH buttons exist on the screen -> high element overlap -> a same-screen layout shift.
_ARRIVAL = _frame("com.app", _btn("com.app", "go", "Go", 500, 620), _btn("com.app", "more", "More", 700, 820))
_SHIFTED = _frame("com.app", _btn("com.app", "more", "More", 500, 620), _btn("com.app", "go", "Go", 700, 820))


def test_tap_binds_to_fresh_layout_same_screen():
    drv = FakeDriver(
        hierarchies=[_ARRIVAL] * 3 + [_frame("com.app")] * 3,
        dumpsys_pairs=[_dumpsys(NS)] * 3 + [_dumpsys("com.app/.BActivity")] * 3,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()  # arrival snapshot: "go" at the tap point
    s._fresh = _fresh_dict(_SHIFTED, NS)  # by tap time, "more" is at the tap point
    t = s.record_gesture(_tap())
    assert t is not None
    assert t.action.selector.value == "More"  # bound to the live layout, not the stale "Go"
    assert s.graph.g.number_of_nodes() == 2  # no phantom node (same screen)


# ---- dropped-navigation reconcile: the live screen is a DIFFERENT screen ----

_HOME = _frame("com.app", _btn("com.app", "feed_post", "Feed"))
_INBOX = _frame("com.app", _btn("com.app", "convo_robertin", "Robertín"))  # low overlap vs home


def test_dropped_navigation_reconciles_and_attributes_tap_to_real_screen():
    events = []
    drv = FakeDriver(
        hierarchies=[_HOME] * 3 + [_frame("com.app")] * 3,  # arrival home; tap -> a target screen
        dumpsys_pairs=[_dumpsys(NS)] * 3 + [_dumpsys("com.app/.ChatActivity")] * 3,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, sink=events.append, settle_kwargs=NOSLEEP)
    home = s.start()
    # the home->inbox tap was DROPPED; we're really on the inbox now
    s._fresh = _fresh_dict(_INBOX, NS, stable=2)
    inbox_id = s._fresh["id"]
    t = s.record_gesture(_tap())  # tapped Robertín IN THE INBOX

    assert any(e.get("event_type") == "implicit_screen_change" for e in events)
    assert inbox_id in s.graph.g.nodes  # the real (inbox) screen was materialized
    assert t is not None and t.source == inbox_id  # edge attributed to inbox, NOT home
    assert t.source != home.id


def test_no_reconcile_when_overlap_high_collapse():
    # a collapse changes structure but keeps most elements -> NOT a navigation
    collapsed = _frame("com.app", _btn("com.app", "go", "Go"),
                       '<node class="android.widget.TextView" package="com.app" resource-id="" '
                       'clickable="false" content-desc="" text="x" bounds="[0,900][50,950]"/>')
    drv = FakeDriver(
        hierarchies=[_ARRIVAL] * 3 + [_frame("com.app")] * 3,
        dumpsys_pairs=[_dumpsys(NS)] * 6,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    before = s.current_id
    s._fresh = _fresh_dict(collapsed, NS, stable=2)
    s.record_gesture(_tap())
    # same screen kept (overlap high) — no phantom node minted from the collapse
    assert s.graph.screen(before) is not None


def test_no_reconcile_when_not_stable():
    drv = FakeDriver(
        hierarchies=[_HOME] * 3 + [_frame("com.app")] * 3,
        dumpsys_pairs=[_dumpsys(NS)] * 3 + [_dumpsys("com.app/.BActivity")] * 3,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    home = s.start()
    s._fresh = _fresh_dict(_INBOX, NS, stable=0)  # only seen once -> don't trust as a real screen
    t = s.record_gesture(_tap())
    assert t.source == home.id  # no reconcile on an unstable single sighting


def _midload(pkg="com.app"):
    """A lagging mid-load frame: the only node at the list region is an unlabeled,
    non-clickable loading overlay (the task-#17a flake shape)."""
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.FrameLayout" package="{pkg}" resource-id="{pkg}:id/loading_container" '
            'clickable="false" content-desc="" text="" bounds="[0,200][1080,2200]"/></node></hierarchy>')


def test_tap_binds_against_arrival_when_fresh_frame_is_midload():
    # the refresher lagged (its last completed dump is the loading overlay) but the arrival
    # snapshot has the loaded row: the tap must bind text-semantically, never to the overlay.
    loaded = _frame("com.app", _btn("com.app", "row", "Internet"))
    drv = FakeDriver(hierarchies=[loaded] * 3 + [_frame("com.app", _btn("com.app", "x", "Next"))] * 3,
                     dumpsys_pairs=[_dumpsys(NS)] * 3 + [_dumpsys("com.app/.B")] * 3,
                     display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()                                         # arrival snapshot = loaded
    s._fresh = _fresh_dict(_midload(), NS, stable=0)  # refresher caught a still-loading frame
    t = s.record_gesture(Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560))
    assert t.action.selector.kind == "label" and t.action.selector.value == "Internet"
    assert not t.needs_confirmation


def test_tap_binds_against_fresh_when_it_is_the_plausible_one():
    # symmetric: arrival is stale/mid-load, the refresher has the loaded layout (today's path).
    midload = _midload()
    drv = FakeDriver(hierarchies=[midload] * 3 + [_frame("com.app", _btn("com.app", "x", "Next"))] * 3,
                     dumpsys_pairs=[_dumpsys(NS)] * 3 + [_dumpsys("com.app/.B")] * 3,
                     display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()                                  # arrival snapshot = mid-load
    s._fresh = _fresh_dict(_frame("com.app", _btn("com.app", "row", "Internet")), NS)
    t = s.record_gesture(Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560))
    assert t.action.selector.kind == "label" and t.action.selector.value == "Internet"


def test_tap_with_no_plausible_frame_is_provisional_never_a_confident_rid():
    # both frames show only the overlay at the point: the recorder must NOT confidently
    # record the overlay's resource_id (the on-device replay failure) — the edge records,
    # honestly tagged provisional.
    midload = _midload()
    drv = FakeDriver(hierarchies=[midload] * 3 + [_frame("com.app", _btn("com.app", "x", "Next"))] * 3,
                     dumpsys_pairs=[_dumpsys(NS)] * 3 + [_dumpsys("com.app/.B")] * 3,
                     display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s._fresh = _fresh_dict(midload, NS)
    t = s.record_gesture(Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=1000))
    assert t.needs_confirmation


class _ScriptedStop:
    """Stand-in for the refresher's stop Event: records each requested wait and stops the
    loop after `cycles` iterations (mirrors threading.Event.wait -> bool)."""
    def __init__(self, cycles):
        self.waits, self._left = [], cycles
    def wait(self, t):
        self.waits.append(round(t, 3))
        self._left -= 1
        return self._left < 0


def test_refresher_bursts_through_instability_then_relaxes():
    # mid-load frames differ cycle to cycle (fp churn) -> the refresher must re-dump
    # back-to-back (riding uiautomator's idle gate) instead of sleeping a full interval on a
    # known-stale frame; once a dump repeats AND the frame has an affordance, it relaxes.
    loaded = _frame("com.app", _btn("com.app", "row", "Internet"))
    mid1 = _midload()
    mid2 = _midload().replace("loading_container", "loading_container2")
    drv = FakeDriver(hierarchies=[mid1, mid2, loaded, loaded, loaded],
                     dumpsys_pairs=[_dumpsys(NS)] * 5, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s._refresh_stop = _ScriptedStop(cycles=5)
    s._refresh_loop()
    w = s._refresh_stop.waits
    assert w[0] == s.refresh_interval            # initial cadence
    assert w[1] == s.refresh_burst_interval      # after mid1 (unstable, affordance-less)
    assert w[2] == s.refresh_burst_interval      # after mid2 (unstable)
    assert w[3] == s.refresh_burst_interval      # after loaded#1 (stable not yet held)
    assert w[4] == s.refresh_interval            # loaded#2 == loaded#1 -> stable -> relax


def test_refresher_burst_is_bounded_for_never_settling_screens():
    # a feed that never repeats a fingerprint must not pin the CPU: after the burst budget
    # the refresher falls back to the normal cadence.
    frames = [_frame("com.app", _btn("com.app", f"r{i}", f"Item {i}")) for i in range(12)]
    drv = FakeDriver(hierarchies=frames, dumpsys_pairs=[_dumpsys(NS)] * 12, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s._refresh_stop = _ScriptedStop(cycles=12)
    s._refresh_loop()
    assert s.refresh_interval in s._refresh_stop.waits[1 + s.refresh_burst_budget:]


def test_refresher_burst_is_bounded_when_namespace_flips_every_cycle():
    # adversarial HIGH: when the foreground namespace flips every cycle, the streak reset on
    # "new screen" meant the burst budget never bound -> a 50Hz busy-wait forever (CPU pin,
    # no-blind-sleep regression). The bound must hold regardless of namespace churn.
    frames = [_midload()] * 12                         # affordance-less -> never relaxes
    dumps = [_dumpsys(f"com.app/.A{i % 2}") for i in range(12)]  # ns alternates every cycle
    drv = FakeDriver(hierarchies=frames, dumpsys_pairs=dumps, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s._refresh_stop = _ScriptedStop(cycles=12)
    s._refresh_loop()
    assert s.refresh_interval in s._refresh_stop.waits[1 + s.refresh_burst_budget:]
