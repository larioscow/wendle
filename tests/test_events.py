from pathlib import Path

from wendle.capture.events import parse_getevent_stream

FIX = Path(__file__).parent / "fixtures"


def test_parses_labeled_event_lines():
    text = (FIX / "getevent_lt_tap.txt").read_text()
    events = parse_getevent_stream(text)
    # 7 event lines in the fixture
    assert len(events) == 7
    first = events[0]
    assert first.ts == 86753.123456
    assert first.type == "EV_ABS"
    assert first.code == "ABS_MT_TRACKING_ID"
    assert first.value == "00000042"
    # BTN_TOUCH UP is the second-to-last line
    assert events[-2].code == "BTN_TOUCH"
    assert events[-2].value == "UP"


def test_skips_non_matching_lines():
    text = "add device 1: /dev/input/event3\ngarbage\n[ 1.5] /dev/input/event3: EV_SYN SYN_REPORT 00000000\n"
    events = parse_getevent_stream(text)
    assert len(events) == 1
    assert events[0].code == "SYN_REPORT"
