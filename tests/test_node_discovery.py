from pathlib import Path

import pytest

from wendle.calibration.getevent_parse import parse_getevent_lp
from wendle.calibration.node_discovery import (
    NoTouchscreenError,
    find_touchscreen,
)

FIX = Path(__file__).parent / "fixtures"


def _devs(name):
    return parse_getevent_lp((FIX / name).read_text())


def test_finds_touchscreen_on_pixel():
    ts = find_touchscreen(_devs("getevent_lp_pixel.txt"))
    assert ts.path == "/dev/input/event3"


def test_finds_touchscreen_not_first_node_on_xiaomi():
    ts = find_touchscreen(_devs("getevent_lp_xiaomi.txt"))
    assert ts.path == "/dev/input/event5"


def test_raises_when_no_touchscreen():
    with pytest.raises(NoTouchscreenError):
        find_touchscreen(_devs("getevent_lp_pixel.txt")[:1])
