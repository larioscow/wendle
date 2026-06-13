from pathlib import Path

from wendle.calibration.timebase import parse_lt_timestamps, validate_timebase

FIX = Path(__file__).parent / "fixtures"


def test_parses_monotonic_timestamps():
    text = (FIX / "getevent_lt_tap.txt").read_text()
    ts = parse_lt_timestamps(text)
    assert ts[0] == 86753.123456
    assert ts[-1] == 86753.245678


def test_validate_passes_when_event_in_window():
    assert validate_timebase(observed=86753.123456, expected_lo=86753.10, expected_hi=86753.30) is True


def test_validate_fails_when_event_out_of_window():
    assert validate_timebase(observed=1748678400.0, expected_lo=86753.10, expected_hi=86753.30) is False
