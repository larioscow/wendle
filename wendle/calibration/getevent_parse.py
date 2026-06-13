from __future__ import annotations

import re
from typing import List, Optional

from wendle.models import AbsAxis, InputDevice

_ADD = re.compile(r"^add device \d+:\s*(?P<path>\S+)")
_NAME = re.compile(r'^\s*name:\s*"(?P<name>.*)"')
_AXIS = re.compile(
    r"^\s*(?P<label>ABS_[A-Z0-9_]+)\s*:\s*value\s*-?\d+,\s*"
    r"min\s*(?P<min>-?\d+),\s*max\s*(?P<max>-?\d+)"
)


def parse_getevent_lp(text: str) -> List[InputDevice]:
    """Parse `getevent -lp` output into a list of InputDevice.

    Line-oriented and tolerant of CRLF, blank lines, and trailing axis fields
    (fuzz/flat/resolution) so it survives OEM formatting differences.
    """
    devices: List[InputDevice] = []
    current: Optional[InputDevice] = None
    for line in text.splitlines():
        m = _ADD.match(line)
        if m:
            current = InputDevice(path=m.group("path"), name="")
            devices.append(current)
            continue
        if current is None:
            continue
        m = _NAME.match(line)
        if m:
            current.name = m.group("name")
            continue
        m = _AXIS.match(line)
        if m:
            current.abs_axes[m.group("label")] = AbsAxis(
                min=int(m.group("min")), max=int(m.group("max"))
            )
    return devices
