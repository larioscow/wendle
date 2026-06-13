"""Tap-vs-swipe is decided in DISPLAY pixels, not raw panel units.

On-device regression: a Galaxy S23 reports a 4095x4095 touch panel on a 1440x3088
display, so a raw delta of ~50 is only ~18 px wide — an ordinary tap that drifts a few
pixels was misrecorded as a swipe (and replayed as a non-navigating click).
"""
from wendle.capture.protocols.base import make_gesture
from wendle.capture.protocols.type_b import TypeBProtocol
from wendle.capture.types import InputEvent

# S23-like panel->display scale
X_SCALE = 1440 / 4095
Y_SCALE = 3088 / 4095


def _g(sx, sy, ex, ey, **kw):
    return make_gesture(
        t_down=1.0, t_up=1.1, sx=sx, sy=sy, ex=ex, ey=ey, multi=False,
        position_missing=False, long_press_s=0.5, swipe_dist=48, **kw,
    )


def test_small_raw_drift_is_a_tap_when_scaled():
    # 60 raw units of drift = ~21px x / ~45px y on screen -> still a TAP
    g = _g(2000, 1500, 2060, 1500, x_scale=X_SCALE, y_scale=Y_SCALE)
    assert g.kind == "tap"


def test_real_swipe_is_still_a_swipe_when_scaled():
    # 800 raw units = ~281px -> clearly a swipe
    g = _g(2000, 1500, 2000, 2300, x_scale=X_SCALE, y_scale=Y_SCALE)
    assert g.kind == "swipe"


def test_unscaled_default_preserves_raw_behavior():
    # without scales (raw), 60 >= 48 -> swipe (the legacy/raw unit-test contract)
    g = _g(2000, 1500, 2060, 1500)
    assert g.kind == "swipe"


def test_decode_threads_scales_through():
    # a tiny type_b contact with ~50 raw drift decodes to a TAP under display scaling
    evs = [
        InputEvent(ts=1.0, type="EV_ABS", code="ABS_MT_TRACKING_ID", value="00000001"),
        InputEvent(ts=1.0, type="EV_ABS", code="ABS_MT_POSITION_X", value="000007d0"),  # 2000
        InputEvent(ts=1.0, type="EV_ABS", code="ABS_MT_POSITION_Y", value="000005dc"),  # 1500
        InputEvent(ts=1.05, type="EV_ABS", code="ABS_MT_POSITION_X", value="00000802"),  # 2050
        InputEvent(ts=1.10, type="EV_ABS", code="ABS_MT_TRACKING_ID", value="ffffffff"),
    ]
    out = TypeBProtocol().decode(evs, swipe_dist=48, x_scale=X_SCALE, y_scale=Y_SCALE)
    assert len(out) == 1 and out[0].kind == "tap"
