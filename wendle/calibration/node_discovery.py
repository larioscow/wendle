from __future__ import annotations

from typing import List

from wendle.models import InputDevice


class NoTouchscreenError(Exception):
    """Raised when no input device looks like a multi-touch touchscreen."""


def _is_touchscreen(dev: InputDevice) -> bool:
    return "ABS_MT_POSITION_X" in dev.abs_axes and "ABS_MT_TOUCH_MAJOR" in dev.abs_axes


def find_touchscreen(devices: List[InputDevice]) -> InputDevice:
    """Pick the touchscreen node by its ABS_MT signature.

    Never hardcodes eventN. If several qualify, prefer the largest reported
    X*Y area (the real screen, not a small aux panel).
    """
    candidates = [d for d in devices if _is_touchscreen(d)]
    if not candidates:
        raise NoTouchscreenError(
            "no input device exposes both ABS_MT_POSITION_X and ABS_MT_TOUCH_MAJOR"
        )
    if len(candidates) == 1:
        return candidates[0]
    return max(
        candidates,
        key=lambda d: d.abs_axes["ABS_MT_POSITION_X"].max
        * d.abs_axes["ABS_MT_POSITION_Y"].max,
    )
