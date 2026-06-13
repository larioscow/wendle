"""§2.7 reveal classification + §2.8 wrong-merge tripwire (lazy-region design).

A swipe is replayable `reveal` ONLY when region-bound and content-advance; retreat sense
(the pull-to-refresh shape) and pans on region-free screens are `probe`. A same-id TAP
across materially different region content is an invisible navigation after collapse —
recorded as a SUSPECT edge (flagged, low-confidence), never swallowed by the
effectiveness filter.
"""
from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
NOSLEEP = {"sleep": lambda _dt: None}
PKG = "com.app"
NS = f"{PKG}/.FeedActivity"


def _feed(labels, btn="Go"):
    """A screen with chrome (button) + an anonymous scrollable 4-row region (D2)."""
    rows = "".join(
        f'<node class="android.view.View" package="{PKG}" resource-id="" clickable="true" '
        f'checkable="false" focusable="false" content-desc="" text="" scrollable="false" '
        f'bounds="[0,{600 + i * 300}][1080,{900 + i * 300}]">'
        f'<node class="android.widget.TextView" package="{PKG}" resource-id="" clickable="false" '
        f'checkable="false" focusable="false" content-desc="" text="{lab}" scrollable="false" '
        f'bounds="[40,{610 + i * 300}][1000,{690 + i * 300}]"/></node>'
        for i, lab in enumerate(labels)
    )
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{PKG}" resource-id="{PKG}:id/go" '
        f'clickable="true" checkable="false" content-desc="" text="{btn}" '
        f'bounds="[40,200][1040,320]"/>'
        f'<node class="android.view.View" package="{PKG}" resource-id="" clickable="false" '
        f'checkable="false" focusable="false" content-desc="" text="" scrollable="true" '
        f'bounds="[0,600][1080,2100]">{rows}</node>'
        f"</node></hierarchy>"
    )


def _dumpsys():
    return (f"topResumedActivity: ActivityRecord{{x u0 {PKG}/.FeedActivity t1}}",
            f"mCurrentFocus=Window{{x u0 {PKG}/.FeedActivity}}")


def _session(*frames):
    hs, ds = [], []
    for xml in frames:
        hs += [xml] * 3
        ds += [_dumpsys()] * 3
    drv = FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP, live_refresh=False)
    s.start()
    return s


ROWS_1 = ["Alpha", "Beta", "Gamma", "Delta"]
ROWS_2 = ["Echo", "Foxtrot", "Golf", "Hotel"]  # same shapes, different content window


def _swipe(y, y2):
    return Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=y, x2=540, y2=y2)


def test_region_content_advance_swipe_is_reveal():
    s = _session(_feed(ROWS_1), _feed(ROWS_1))
    assert s.record_gesture(_swipe(1900, 700)) is None  # up-swipe inside the region
    scr = s.graph.screen(s.current_id)
    assert scr.intra_actions[-1].intent == "reveal"


def test_retreat_sense_swipe_is_probe_never_replayable():
    # the pull-to-refresh shape: same screen, region-bound, but RETREAT sense
    s = _session(_feed(ROWS_1), _feed(ROWS_1))
    assert s.record_gesture(_swipe(700, 1900)) is None  # down-swipe
    scr = s.graph.screen(s.current_id)
    assert scr.intra_actions[-1].intent == "probe"


def test_swipe_outside_any_region_is_probe():
    s = _session(_feed(ROWS_1), _feed(ROWS_1))
    # start point in the chrome area (y=400), not inside the region container
    g = Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=400, x2=540, y2=250)
    assert s.record_gesture(g) is None
    scr = s.graph.screen(s.current_id)
    assert scr.intra_actions[-1].intent == "probe"


def _tap_button():
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=260)  # the Go button


def test_same_id_tap_over_changed_region_content_records_suspect_edge():
    # §2.8: after collapse, ROWS_1 and ROWS_2 hash IDENTICALLY — a tap that actually
    # navigated between two such pages is invisible to identity. The tripwire records it
    # as a flagged low-confidence edge instead of swallowing it as a probe.
    events = []
    s = _session(_feed(ROWS_1), _feed(ROWS_2))
    s.sink = events.append
    t = s.record_gesture(_tap_button())
    assert t is not None and t.source == t.target  # same id (the collapse merge)
    assert t.suspect_self_loop is True and t.needs_confirmation is True
    (_, _, _, data), = list(s.graph.ordered_transitions())
    assert data["suspect_self_loop"] is True
    assert any(e.get("event_type") == "suspect_self_loop" for e in events)


def _chrome(big):
    """A collapsing-toolbar header: expanded = title + a search FIELD; collapsed = one
    small title. STRUCTURALLY different (the field's node tuple survives value
    suppression and consecutive-dedup), so both identity tiers genuinely fork across the
    scroll — the S23-confirmed OEM default (design doc Q5; the on-device Settings fork
    produced two plain, non-twin nodes)."""
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


def test_chrome_forked_scroll_is_a_reveal_scroll_edge_not_a_navigate_edge():
    # The collapsing toolbar forks BOTH tiers, so coarse-id equality fails — yet the gesture
    # is a scroll of the same logical screen. Region CONTINUITY classifies it reveal. Cap 1:
    # it is recorded as a `scroll`-CLASS continuation edge (intent='reveal'), NOT a navigate
    # edge — the gesture is intra evidence on the source, the session follows the forked node,
    # and replay/navigate treat the scroll class specially (skip / reveal-walk), never as a tap.
    from wendle.fingerprint.signature import fingerprint
    before = _forked(True, ["Alpha", "Beta", "Gamma", "Delta"])
    after = _forked(False, ["Gamma", "Delta", "Echo", "Foxtrot"])
    assert fingerprint(NS, before) != fingerprint(NS, after)  # the fork is real
    s = _session(before, after)
    src_id = s.current_id
    t = s.record_gesture(_swipe(1800, 700))
    assert t is not None and t.action_class == "scroll" and t.action.intent == "reveal"
    assert s.graph.screen(src_id).intra_actions[-1].intent == "reveal"
    assert s.current_id != src_id  # the session followed the forked node
    # NO navigate-class edge was minted (only the scroll continuation)
    assert all(d.get("action_class") == "scroll"
               for (_u, _v, _k, d) in s.graph.ordered_transitions())


def test_chrome_forked_horizontal_swipe_stays_a_navigate_edge():
    # axis mismatch (a row-level horizontal swipe in a vertical region) is NOT continuity:
    # if the ids fork, the edge records as a real navigation (archive/dismiss semantics).
    before = _forked(True, ["Alpha", "Beta", "Gamma", "Delta"])
    after = _forked(False, ["Gamma", "Delta", "Echo", "Foxtrot"])
    s = _session(before, after)
    g = Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=200, y=900, x2=900, y2=900)
    t = s.record_gesture(g)
    assert t is not None and t.action.action_type == "swipe"


def test_same_id_tap_over_identical_content_stays_a_probe():
    s = _session(_feed(ROWS_1), _feed(ROWS_1))
    assert s.record_gesture(_tap_button()) is None  # effectiveness filter, unchanged
    scr = s.graph.screen(s.current_id)
    assert scr.intra_actions[-1].intent == "probe"
    assert s.graph.g.number_of_edges() == 0
