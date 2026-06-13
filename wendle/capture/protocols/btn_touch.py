from __future__ import annotations

from typing import List, Optional

from wendle.capture.protocols.base import TouchProtocol, hex_value, make_gesture
from wendle.capture.types import Gesture, InputEvent


class BtnTouchProtocol(TouchProtocol):
    """BTN_TOUCH-driven decoding (single-touch / type-A devices).

    A gesture is bounded by BTN_TOUCH DOWN/UP; position is taken from the
    ABS_MT_POSITION_X/Y seen either just before DOWN or in the same frame just
    after it. Any ABS_MT_SLOT > 0 between contacts flags the gesture `multi`,
    including a second finger that lands in the opening frame. A touch still
    held at end of stream is flushed as `truncated`.
    """

    name = "btn_touch"

    def decode(
        self, events: List[InputEvent], *, long_press_s: float = 0.5, swipe_dist: int = 30,
        x_scale: float = 1.0, y_scale: float = 1.0,
    ) -> List[Gesture]:
        gestures: List[Gesture] = []
        active = False
        max_slot = 0
        t_down = 0.0
        last_x: Optional[int] = None
        last_y: Optional[int] = None
        start_x: Optional[int] = None
        start_y: Optional[int] = None
        last_ts = 0.0

        def emit(t_up: float, *, truncated: bool = False) -> None:
            position_missing = start_x is None or start_y is None
            sx = start_x if start_x is not None else 0
            sy = start_y if start_y is not None else 0
            ex = last_x if last_x is not None else sx
            ey = last_y if last_y is not None else sy
            gestures.append(
                make_gesture(
                    t_down=t_down,
                    t_up=t_up,
                    sx=sx,
                    sy=sy,
                    ex=ex,
                    ey=ey,
                    multi=max_slot > 0,
                    position_missing=position_missing,
                    long_press_s=long_press_s,
                    swipe_dist=swipe_dist,
                    truncated=truncated,
                    x_scale=x_scale,
                    y_scale=y_scale,
                )
            )

        for ev in events:
            last_ts = ev.ts
            if ev.code == "ABS_MT_POSITION_X":
                v = hex_value(ev.value)
                if v is not None:
                    last_x = v
                    if active and start_x is None:  # POSITION-after-DOWN ordering
                        start_x = v
            elif ev.code == "ABS_MT_POSITION_Y":
                v = hex_value(ev.value)
                if v is not None:
                    last_y = v
                    if active and start_y is None:
                        start_y = v
            elif ev.code == "ABS_MT_SLOT":
                v = hex_value(ev.value)
                if v is not None:
                    max_slot = max(max_slot, v)
            elif ev.code == "BTN_TOUCH":
                if ev.value.upper() == "DOWN":
                    active = True
                    t_down = ev.ts
                    start_x, start_y = last_x, last_y  # POSITION-before-DOWN ordering
                elif ev.value.upper() == "UP" and active:
                    emit(ev.ts)
                    # reset all per-gesture state to prevent stale leakage
                    active = False
                    max_slot = 0
                    last_x = last_y = start_x = start_y = None

        if active:  # touch never lifted → flush truncated
            emit(last_ts, truncated=True)

        return gestures
