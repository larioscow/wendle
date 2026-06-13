from wendle.capture.gestures import segment_gestures
from wendle.capture.types import InputEvent


def _ev(ts, code, value, type_="EV_ABS"):
    return InputEvent(ts=ts, type=type_, code=code, value=value)


def _touch(ts_down, ts_up, x, y, *, x2=None, y2=None, slot0_only=True):
    """Build a minimal down→up event sequence at hex-encoded coords."""
    evs = [
        _ev(ts_down, "ABS_MT_TRACKING_ID", "00000001"),
        _ev(ts_down, "ABS_MT_POSITION_X", f"{x:08x}"),
        _ev(ts_down, "ABS_MT_POSITION_Y", f"{y:08x}"),
        _ev(ts_down, "BTN_TOUCH", "DOWN", "EV_KEY"),
        _ev(ts_down, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    if x2 is not None:
        evs += [
            _ev((ts_down + ts_up) / 2, "ABS_MT_POSITION_X", f"{x2:08x}"),
            _ev((ts_down + ts_up) / 2, "ABS_MT_POSITION_Y", f"{y2:08x}"),
            _ev((ts_down + ts_up) / 2, "SYN_REPORT", "00000000", "EV_SYN"),
        ]
    evs += [
        _ev(ts_up, "BTN_TOUCH", "UP", "EV_KEY"),
        _ev(ts_up, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    return evs


def test_short_press_is_tap():
    g = segment_gestures(_touch(10.0, 10.08, 500, 600))
    assert len(g) == 1
    assert g[0].kind == "tap"
    assert (g[0].x, g[0].y) == (500, 600)


def test_long_dwell_is_long_press():
    g = segment_gestures(_touch(10.0, 10.9, 500, 600))
    assert g[0].kind == "long_press"


def test_displacement_is_swipe():
    g = segment_gestures(_touch(10.0, 10.2, 500, 600, x2=500, y2=900))
    assert g[0].kind == "swipe"
    assert (g[0].x2, g[0].y2) == (500, 900)


def test_multi_finger_is_flagged_not_a_tap():
    evs = _touch(10.0, 10.08, 500, 600)
    # inject a second active slot while the touch is down
    evs.insert(4, InputEvent(ts=10.01, type="EV_ABS", code="ABS_MT_SLOT", value="00000001"))
    g = segment_gestures(evs)
    assert g[0].kind == "multi"


def test_two_taps_segment_independently():
    g = segment_gestures(_touch(10.0, 10.05, 100, 200) + _touch(11.0, 11.05, 300, 400))
    assert [x.kind for x in g] == ["tap", "tap"]
    assert (g[1].x, g[1].y) == (300, 400)


def test_up_without_down_is_ignored():
    evs = [
        _ev(5.0, "BTN_TOUCH", "UP", "EV_KEY"),
        _ev(5.0, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    assert segment_gestures(evs) == []


def test_missing_position_is_flagged():
    evs = [
        _ev(10.0, "BTN_TOUCH", "DOWN", "EV_KEY"),
        _ev(10.0, "SYN_REPORT", "00000000", "EV_SYN"),
        _ev(10.05, "BTN_TOUCH", "UP", "EV_KEY"),
        _ev(10.05, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    g = segment_gestures(evs)
    assert len(g) == 1
    assert g[0].position_missing is True
