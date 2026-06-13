from pathlib import Path

from wendle.calibration.getevent_parse import parse_getevent_lp

FIX = Path(__file__).parent / "fixtures"


def test_parses_devices_and_axes():
    text = (FIX / "getevent_lp_pixel.txt").read_text()
    devices = parse_getevent_lp(text)
    by_path = {d.path: d for d in devices}
    assert set(by_path) == {"/dev/input/event0", "/dev/input/event3"}
    ts = by_path["/dev/input/event3"]
    assert ts.name == "ft5x46_ts"
    assert ts.abs_axes["ABS_MT_POSITION_X"].max == 1080
    assert ts.abs_axes["ABS_MT_POSITION_Y"].max == 2340
    assert "ABS_MT_TOUCH_MAJOR" in ts.abs_axes
    assert by_path["/dev/input/event0"].abs_axes == {}


def test_tolerates_blank_lines_and_crlf():
    text = (
        "add device 1: /dev/input/event3\r\n"
        '  name:     "ts"\r\n'
        "\r\n"
        "  events:\r\n"
        "    ABS (0003):\r\n"
        "      ABS_MT_POSITION_X     : value 0, min 0, max 720, fuzz 0, flat 0, resolution 0\r\n"
    )
    devices = parse_getevent_lp(text)
    assert devices[0].abs_axes["ABS_MT_POSITION_X"].max == 720
