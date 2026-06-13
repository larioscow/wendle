from wendle.capture.protocols.type_b import TypeBProtocol
from wendle.capture.types import InputEvent

P = TypeBProtocol()


def _ev(ts, code, value, type_="EV_ABS"):
    return InputEvent(ts=ts, type=type_, code=code, value=value)


def _touch(t_down, t_up, x, y, *, slot=0, x2=None, y2=None):
    """A type-B single-finger contact: tracking-id down → positions → -1 up."""
    evs = [
        _ev(t_down, "ABS_MT_SLOT", f"{slot:08x}"),
        _ev(t_down, "ABS_MT_TRACKING_ID", "00000abc"),
        _ev(t_down, "ABS_MT_POSITION_X", f"{x:08x}"),
        _ev(t_down, "ABS_MT_POSITION_Y", f"{y:08x}"),
        _ev(t_down, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    if x2 is not None:
        evs += [
            _ev((t_down + t_up) / 2, "ABS_MT_POSITION_X", f"{x2:08x}"),
            _ev((t_down + t_up) / 2, "ABS_MT_POSITION_Y", f"{y2:08x}"),
            _ev((t_down + t_up) / 2, "SYN_REPORT", "00000000", "EV_SYN"),
        ]
    evs += [
        _ev(t_up, "ABS_MT_SLOT", f"{slot:08x}"),
        _ev(t_up, "ABS_MT_TRACKING_ID", "ffffffff"),
        _ev(t_up, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    return evs


def test_single_tap():
    g = P.decode(_touch(10.0, 10.08, 500, 600))
    assert len(g) == 1
    assert g[0].kind == "tap"
    assert (g[0].x, g[0].y) == (500, 600)


def test_long_press():
    g = P.decode(_touch(10.0, 10.9, 500, 600))
    assert g[0].kind == "long_press"


def test_swipe():
    g = P.decode(_touch(10.0, 10.2, 500, 600, x2=500, y2=900))
    assert g[0].kind == "swipe"
    assert (g[0].x2, g[0].y2) == (500, 900)


def test_sequential_taps_are_not_multi():
    g = P.decode(_touch(10.0, 10.05, 100, 200) + _touch(11.0, 11.05, 300, 400))
    assert [x.kind for x in g] == ["tap", "tap"]


def test_two_fingers_concurrent_is_multi():
    # slot 0 down, slot 1 down (concurrent), both up
    evs = [
        _ev(10.0, "ABS_MT_SLOT", "00000000"),
        _ev(10.0, "ABS_MT_TRACKING_ID", "00000001"),
        _ev(10.0, "ABS_MT_POSITION_X", f"{100:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{200:08x}"),
        _ev(10.0, "SYN_REPORT", "00000000", "EV_SYN"),
        _ev(10.02, "ABS_MT_SLOT", "00000001"),
        _ev(10.02, "ABS_MT_TRACKING_ID", "00000002"),
        _ev(10.02, "ABS_MT_POSITION_X", f"{800:08x}"),
        _ev(10.02, "ABS_MT_POSITION_Y", f"{900:08x}"),
        _ev(10.02, "SYN_REPORT", "00000000", "EV_SYN"),
        _ev(10.1, "ABS_MT_SLOT", "00000000"),
        _ev(10.1, "ABS_MT_TRACKING_ID", "ffffffff"),
        _ev(10.1, "ABS_MT_SLOT", "00000001"),
        _ev(10.1, "ABS_MT_TRACKING_ID", "ffffffff"),
        _ev(10.1, "SYN_REPORT", "00000000", "EV_SYN"),
    ]
    g = P.decode(evs)
    assert len(g) == 2
    assert all(x.kind == "multi" for x in g)


def test_default_slot_zero_without_explicit_slot_event():
    # kernel omits ABS_MT_SLOT when it stays 0
    evs = [
        _ev(10.0, "ABS_MT_TRACKING_ID", "00000005"),
        _ev(10.0, "ABS_MT_POSITION_X", f"{250:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{350:08x}"),
        _ev(10.04, "ABS_MT_TRACKING_ID", "ffffffff"),
    ]
    g = P.decode(evs)
    assert len(g) == 1
    assert (g[0].x, g[0].y) == (250, 350)


def test_implicit_finger_replacement_emits_both_contacts():
    # same slot gets a new tracking-id with no intervening -1: close old, open new
    evs = (
        _touch(10.0, 10.05, 100, 200)[:-3]  # drop the lift of the first
        + [
            _ev(10.06, "ABS_MT_TRACKING_ID", "00000099"),
            _ev(10.06, "ABS_MT_POSITION_X", f"{300:08x}"),
            _ev(10.06, "ABS_MT_POSITION_Y", f"{400:08x}"),
            _ev(10.1, "ABS_MT_TRACKING_ID", "ffffffff"),
        ]
    )
    g = P.decode(evs)
    assert len(g) == 2
    assert (g[0].x, g[0].y) == (100, 200)
    assert (g[1].x, g[1].y) == (300, 400)


def test_dangling_contact_flushed_truncated():
    evs = [
        _ev(10.0, "ABS_MT_TRACKING_ID", "00000001"),
        _ev(10.0, "ABS_MT_POSITION_X", f"{50:08x}"),
        _ev(10.0, "ABS_MT_POSITION_Y", f"{60:08x}"),
        # no lift — stream ends mid-touch
    ]
    g = P.decode(evs)
    assert len(g) == 1
    assert g[0].truncated is True


def test_lift_recognized_across_token_widths():
    for tok in ("ffff", "0xffffffff", "-1"):
        evs = [
            _ev(1.0, "ABS_MT_TRACKING_ID", "00000007"),
            _ev(1.0, "ABS_MT_POSITION_X", f"{10:08x}"),
            _ev(1.0, "ABS_MT_POSITION_Y", f"{20:08x}"),
            _ev(1.04, "ABS_MT_TRACKING_ID", tok),
        ]
        g = P.decode(evs)
        assert len(g) == 1, tok
        assert g[0].truncated is False, tok
