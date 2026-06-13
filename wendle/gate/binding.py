from __future__ import annotations

from typing import List, Optional, Tuple

from wendle.capture.types import Snapshot


def bind_latest(
    snaps: List[Snapshot], t_tap: float, *, stale_after: float = 2.0
) -> Tuple[Optional[Snapshot], str]:
    """Bind a tap to the freshest dump that COMPLETED before it.

    Used by the gate harness, which polls dumps continuously (unlike the real
    recorder's dump-on-transition model, where SnapshotRingBuffer's validity
    windows apply). The hierarchy the user actually saw when tapping is the most
    recent dump finished before the tap. Confidence is HIGH when that dump is
    recent (within `stale_after` seconds), else LOW.

    `snaps` must be sorted by `t_end` ascending. Returns (snapshot, confidence);
    (None, "low") when no dump had completed before the tap.
    """
    prior = [s for s in snaps if s.t_end <= t_tap]
    if not prior:
        return None, "low"
    s = prior[-1]  # newest completed before the tap
    confidence = "high" if (t_tap - s.t_end) <= stale_after else "low"
    return s, confidence
