from __future__ import annotations

from typing import List, Optional

from wendle.capture.protocols.base import TouchProtocol
from wendle.capture.protocols.btn_touch import BtnTouchProtocol
from wendle.capture.types import Gesture, InputEvent


def segment_gestures(
    events: List[InputEvent],
    *,
    long_press_s: float = 0.5,
    swipe_dist: int = 30,
    protocol: Optional[TouchProtocol] = None,
) -> List[Gesture]:
    """Segment a getevent stream into gestures using the given touch protocol.

    `protocol` is selected per-device from `getevent -lp` capabilities (see
    `protocols.select`) and stored in the DeviceProfile. When omitted it
    defaults to BTN_TOUCH decoding for backward compatibility.
    """
    proto = protocol or BtnTouchProtocol()
    return proto.decode(events, long_press_s=long_press_s, swipe_dist=swipe_dist)
