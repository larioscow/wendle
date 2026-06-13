from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class InputEvent:
    """One line of `getevent -lt`, normalized.

    `ts` is the CLOCK_MONOTONIC timestamp (sec.usec). `value` is the raw token
    as printed (hex like "00000087", or "DOWN"/"UP" for BTN_TOUCH).
    """

    ts: float
    type: str  # EV_ABS / EV_KEY / EV_SYN
    code: str  # ABS_MT_POSITION_X / BTN_TOUCH / SYN_REPORT / ...
    value: str


@dataclass
class Gesture:
    """A segmented single-finger gesture in raw touch-panel coordinates."""

    kind: str  # tap / long_press / swipe / multi
    t_down: float
    t_up: float
    x: int
    y: int
    x2: Optional[int] = None  # swipe end
    y2: Optional[int] = None
    position_missing: bool = False  # no ABS position seen → coords are a guess
    truncated: bool = False  # contact never lifted (end of stream) → flushed

    @property
    def duration(self) -> float:
        return self.t_up - self.t_down


@dataclass
class UINode:
    """A node parsed from a UIAutomator hierarchy dump (bounds in pixels)."""

    cls: str
    resource_id: str
    text: str
    content_desc: str
    clickable: bool
    password: bool
    bounds: Tuple[int, int, int, int]  # left, top, right, bottom
    focused: bool = False  # has input focus (for set_text pre/post diffing)
    # Stateful-widget attrs read OUT-OF-BAND (never in the fingerprint — see signature.py):
    # they detect a checkbox/switch/radio flip for a set_checked pre_action.
    checkable: bool = False  # exposes a check/toggle state
    checked: bool = False  # current boolean state
    selected: bool = False  # segmented / tab-style selection
    package: str = ""  # owning package (used to keep system UI / overlays out of tap binding)
    hint_text: str = ""  # placeholder hint (API 26+); an empty field whose text==hint is NOT typed input

    def contains(self, x: int, y: int) -> bool:
        left, top, right, bottom = self.bounds
        return left <= x <= right and top <= y <= bottom

    @property
    def area(self) -> int:
        left, top, right, bottom = self.bounds
        return max(0, right - left) * max(0, bottom - top)

    @property
    def center(self) -> Tuple[int, int]:
        left, top, right, bottom = self.bounds
        return (left + right) // 2, (top + bottom) // 2


@dataclass
class Snapshot:
    """A settled hierarchy snapshot with the device-monotonic window that
    brackets the dump, plus the start time of the NEXT screen transition
    (None = still current). Used for tap-to-hierarchy binding (§5.1)."""

    t_start: float
    t_end: float
    hierarchy_hash: str
    nodes: List[UINode] = field(default_factory=list)
    next_transition: Optional[float] = None
