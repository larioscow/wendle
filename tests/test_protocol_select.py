from pathlib import Path

import pytest

from wendle.calibration.getevent_parse import parse_getevent_lp
from wendle.calibration.node_discovery import find_touchscreen
from wendle.capture.protocols.btn_touch import BtnTouchProtocol
from wendle.capture.protocols.select import (
    detect_protocol_name,
    get_protocol,
    select_protocol,
)
from wendle.capture.protocols.type_b import TypeBProtocol

FIX = Path(__file__).parent / "fixtures"


def _touchscreen(name):
    return find_touchscreen(parse_getevent_lp((FIX / name).read_text()))


def test_pixel_fixture_is_type_b():
    # has ABS_MT_SLOT + ABS_MT_TRACKING_ID
    assert detect_protocol_name(_touchscreen("getevent_lp_pixel.txt")) == "type_b"
    assert isinstance(select_protocol(_touchscreen("getevent_lp_pixel.txt")), TypeBProtocol)


def test_xiaomi_fixture_falls_back_to_btn_touch():
    # no ABS_MT_SLOT
    assert detect_protocol_name(_touchscreen("getevent_lp_xiaomi.txt")) == "btn_touch"
    assert isinstance(select_protocol(_touchscreen("getevent_lp_xiaomi.txt")), BtnTouchProtocol)


def test_get_protocol_unknown_raises():
    with pytest.raises(ValueError):
        get_protocol("nope")
