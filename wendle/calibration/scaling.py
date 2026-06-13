from __future__ import annotations


def scale_to_pixels(raw: int, *, abs_min: int, abs_max: int, screen: int) -> int:
    """Map a raw touch-panel coordinate to a screen pixel.

    getevent X/Y live in the touch-panel range [abs_min, abs_max], which is NOT
    the pixel range; scale into [0, screen-1] and clamp.
    """
    span = abs_max - abs_min
    if span <= 0:
        raise ValueError(f"degenerate axis: min={abs_min} max={abs_max}")
    frac = (raw - abs_min) / span
    px = round(frac * (screen - 1))
    return max(0, min(screen - 1, px))
