from pathlib import Path

from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.recorder import detect_action
from wendle.capture.types import Gesture, Snapshot
from wendle.models import DeviceProfile

FIX = Path(__file__).parent / "fixtures"

# identity profile: panel range == display, so raw coords map 1:1 to pixels
PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3",
    abs_x=(0, 1079),
    abs_y=(0, 2339),
    display=(1080, 2340),
    timebase_validated=True,
)


def _snapshot():
    nodes = parse_hierarchy((FIX / "hierarchy_login.xml").read_text())
    return Snapshot(t_start=0.0, t_end=0.2, hierarchy_hash="login", nodes=nodes)


def _tap(x, y):
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=x, y=y)


def test_tap_on_button_yields_text_click_action():
    action, needs = detect_action(_tap(540, 960), _snapshot(), PROFILE)
    assert action.action_type == "click"
    assert action.selector.kind == "label"
    assert action.selector.value == "Log in"
    assert action.sensitive is False
    assert needs is False


def test_tap_on_password_field_is_redacted():
    action, _ = detect_action(_tap(540, 760), _snapshot(), PROFILE)
    assert action.sensitive is True
    assert action.value == {"param": "password"}
    # secret label never baked into the selector: a password field resolves to
    # resource_id (or coords), never content_desc/text.
    assert action.selector.kind in ("resource_id", "coords")


def test_low_binding_confidence_sets_needs_confirmation():
    _, needs = detect_action(_tap(540, 960), _snapshot(), PROFILE, bind_confidence="low")
    assert needs is True


def test_missing_position_sets_needs_confirmation():
    g = Gesture(kind="tap", t_down=1.0, t_up=1.05, x=0, y=0, position_missing=True)
    _, needs = detect_action(g, _snapshot(), PROFILE)
    assert needs is True


def test_multi_finger_gesture_raises():
    import pytest

    g = Gesture(kind="multi", t_down=1.0, t_up=1.05, x=540, y=960)
    with pytest.raises(ValueError):
        detect_action(g, _snapshot(), PROFILE)


def test_tap_outside_all_bounds_is_coordinate_only():
    # snapshot with a single small node and NO full-screen root, so a tap away
    # from it yields node_at -> None -> coords fallback.
    from wendle.capture.types import UINode

    only = UINode(
        cls="android.widget.Button",
        resource_id="com.app:id/x",
        text="X",
        content_desc="",
        clickable=True,
        password=False,
        bounds=(0, 0, 100, 100),
    )
    snap = Snapshot(t_start=0.0, t_end=0.2, hierarchy_hash="s", nodes=[only])
    action, _ = detect_action(_tap(900, 2000), snap, PROFILE)
    assert action.selector.kind == "coords"
    assert action.replayability == "coordinate_only"


def test_swipe_start_is_coords_even_on_an_element_and_end_carried():
    # A swipe starting ON a button must still bind to START COORDINATES (a drag needs its start
    # point), NOT the button's content_desc. Binding the start to a semantic label was the bug
    # that left swipes with an end but no start -> "swipe missing start/end" at replay.
    g = Gesture(kind="swipe", t_down=1.0, t_up=1.2, x=540, y=960, x2=540, y2=300)
    action, _ = detect_action(g, _snapshot(), PROFILE)
    assert action.action_type == "swipe"
    assert action.selector.kind == "coords"          # the START point, not the element's label
    assert action.replayability == "coordinate_only"
    assert action.end is not None and action.end[1] < action.selector.value[1]  # swiped upward


def test_swipe_off_any_element_is_still_coords():
    g = Gesture(kind="swipe", t_down=1.0, t_up=1.2, x=100, y=200, x2=100, y2=900)
    action, _ = detect_action(g, _snapshot(), PROFILE)
    assert action.selector.kind == "coords" and action.end == (100, 900)


def test_truncated_gesture_needs_confirmation():
    g = Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=960, truncated=True)
    _, needs = detect_action(g, _snapshot(), PROFILE)
    assert needs is True
