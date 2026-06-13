from __future__ import annotations

import re
from typing import List

_TS = re.compile(r"^\[\s*(?P<ts>\d+\.\d+)\]")


def parse_lt_timestamps(text: str) -> List[float]:
    """Extract the CLOCK_MONOTONIC `sec.usec` timestamps from `getevent -lt`."""
    out: List[float] = []
    for line in text.splitlines():
        m = _TS.match(line)
        if m:
            out.append(float(m.group("ts")))
    return out


def validate_timebase(*, observed: float, expected_lo: float, expected_hi: float) -> bool:
    """True if the getevent-reported time falls in the host-derived monotonic window.

    A wall-clock (CLOCK_REALTIME) timestamp lands far outside the boot-relative
    window, so this catches a device whose getevent did not set CLOCK_MONOTONIC.
    """
    return expected_lo <= observed <= expected_hi
