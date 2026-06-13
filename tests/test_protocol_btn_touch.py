from wendle.capture.protocols.btn_touch import BtnTouchProtocol
from wendle.capture.types import InputEvent

P = BtnTouchProtocol()


def _ev(ts, code, value, type_="EV_ABS"):
    return InputEvent(ts=ts, type=type_, code=code, value=value)


def test_position_after_down_is_used_as_start():
    # POSITION arrives AFTER BTN_TOUCH DOWN in the same frame
    evs = [
        _ev(10.0, "BTN_TOUCH", "DOWN", "EV_KEY"),
        _ev(10.0, "ABS_MT_POSITION_X", f"{400:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{500:08x}"),
        _ev(10.0, "SYN_REPORT", "00000000", "EV_SYN"),
        _ev(10.05, "BTN_TOUCH", "UP", "EV_KEY"),
    ]
    g = P.decode(evs)
    assert len(g) == 1
    assert g[0].kind == "tap"
    assert (g[0].x, g[0].y) == (400, 500)
    assert g[0].position_missing is False


def test_multi_finger_in_opening_frame_is_flagged():
    # ABS_MT_SLOT 1 appears BEFORE BTN_TOUCH DOWN
    evs = [
        _ev(10.0, "ABS_MT_SLOT", "00000001"),
        _ev(10.0, "ABS_MT_POSITION_X", f"{100:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{200:08x}"),
        _ev(10.0, "BTN_TOUCH", "DOWN", "EV_KEY"),
        _ev(10.05, "BTN_TOUCH", "UP", "EV_KEY"),
    ]
    assert P.decode(evs)[0].kind == "multi"


def test_no_stale_coord_leak_between_gestures():
    # first tap at (100,200); second touch with NO position must be position_missing,
    # not silently reusing the first tap's coords
    first = [
        _ev(10.0, "ABS_MT_POSITION_X", f"{100:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{200:08x}"),
        _ev(10.0, "BTN_TOUCH", "DOWN", "EV_KEY"),
        _ev(10.05, "BTN_TOUCH", "UP", "EV_KEY"),
    ]
    second = [
        _ev(11.0, "BTN_TOUCH", "DOWN", "EV_KEY"),
        _ev(11.05, "BTN_TOUCH", "UP", "EV_KEY"),
    ]
    g = P.decode(first + second)
    assert len(g) == 2
    assert (g[0].x, g[0].y) == (100, 200)
    assert g[1].position_missing is True


def test_dangling_btn_touch_flushed_truncated():
    evs = [
        _ev(10.0, "ABS_MT_POSITION_X", f"{10:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{20:08x}"),
        _ev(10.0, "BTN_TOUCH", "DOWN", "EV_KEY"),
        # no UP
    ]
    g = P.decode(evs)
    assert len(g) == 1
    assert g[0].truncated is True
