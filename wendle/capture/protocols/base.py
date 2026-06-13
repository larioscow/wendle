from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import List, Optional

from wendle.capture.types import Gesture, InputEvent


def hex_value(value: str) -> Optional[int]:
    """Parse a getevent hex value token; None if not hex (e.g. "DOWN")."""
    try:
        return int(value, 16)
    except ValueError:
        return None


def make_gesture(
    *,
    t_down: float,
    t_up: float,
    sx: int,
    sy: int,
    ex: int,
    ey: int,
    multi: bool,
    position_missing: bool,
    long_press_s: float,
    swipe_dist: int,
    truncated: bool = False,
    x_scale: float = 1.0,
    y_scale: float = 1.0,
) -> Gesture:
    """Classify a finished contact into tap / long_press / swipe / multi.

    `x_scale`/`y_scale` convert raw panel deltas to DISPLAY pixels so the swipe
    threshold is a real on-screen distance. They MUST be passed for live capture: a
    high-resolution touch panel (e.g. 4095 wide on a 1440px display) makes a raw delta
    of 30 only ~10 px, so an ordinary tap's finger drift is misclassified as a swipe.
    Default 1.0 keeps raw-coordinate unit tests unchanged."""
    dist = math.hypot((ex - sx) * x_scale, (ey - sy) * y_scale)
    duration = t_up - t_down
    if multi:
        kind = "multi"
    elif dist >= swipe_dist:
        kind = "swipe"
    elif duration >= long_press_s:
        kind = "long_press"
    else:
        kind = "tap"
    return Gesture(
        kind=kind,
        t_down=t_down,
        t_up=t_up,
        x=sx,
        y=sy,
        x2=ex if kind == "swipe" else None,
        y2=ey if kind == "swipe" else None,
        position_missing=position_missing,
        truncated=truncated,
    )


def is_lift(token: str) -> bool:
    """True if an ABS_MT_TRACKING_ID token signals a finger lift.

    The kernel prints __s32 -1 as an all-F hex string; tolerate any width
    (`ffff`, `ffffffff`, `ffffffffffffffff`), an `0x` prefix, and literal `-1`.
    """
    t = token.strip().lower().removeprefix("0x")
    if t in ("-1",):
        return True
    if t and set(t) == {"f"}:
        return True
    try:
        return int(token, 16) == -1
    except ValueError:
        return False


class TouchProtocol(ABC):
    """Strategy for turning a raw getevent stream into gestures.

    Implementations correspond to evdev touch protocols (NOT device brands):
    `type_b` (ABS_MT_SLOT + ABS_MT_TRACKING_ID, most modern phones), `btn_touch`
    (BTN_TOUCH-driven). The right one is chosen per-device by probing the
    touchscreen's reported capabilities — see `protocols.select`.
    """

    name: str = "base"

    @abstractmethod
    def decode(
        self, events: List[InputEvent], *, long_press_s: float = 0.5, swipe_dist: int = 30,
        x_scale: float = 1.0, y_scale: float = 1.0,
    ) -> List[Gesture]:
        ...
