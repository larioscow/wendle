from __future__ import annotations

import re
from typing import List

from wendle.capture.types import InputEvent

# [   86753.123456] /dev/input/event3: EV_ABS       ABS_MT_POSITION_X    00000087
_LINE = re.compile(
    r"^\[\s*(?P<ts>\d+\.\d+)\]\s+\S+:\s+(?P<type>\S+)\s+(?P<code>\S+)\s+(?P<value>\S+)\s*$"
)


def parse_getevent_stream(text: str) -> List[InputEvent]:
    """Parse `getevent -lt` output into a flat list of InputEvent.

    Lines that don't match the labeled `[ts] node: TYPE CODE VALUE` form are
    skipped (headers, device-add lines), so it tolerates mixed output.
    """
    events: List[InputEvent] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            events.append(
                InputEvent(
                    ts=float(m.group("ts")),
                    type=m.group("type"),
                    code=m.group("code"),
                    value=m.group("value"),
                )
            )
    return events
