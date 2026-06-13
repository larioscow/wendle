from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from wendle.capture.types import Snapshot


@dataclass
class BindResult:
    """Outcome of binding a tap to a hierarchy snapshot (§5.1)."""

    snapshot: Optional[Snapshot]
    confidence: str  # "high" / "low"


class SnapshotRingBuffer:
    """A bounded buffer of recent settled hierarchy snapshots.

    Each snapshot owns a validity window [t_end, next_transition_or_inf). A tap
    binds to the newest snapshot whose window contains t_tap. Binding is HIGH
    confidence when t_tap sits cleanly inside exactly one window with no
    screen-change boundary within `guard` seconds on either side; otherwise LOW
    (flagged for human re-confirmation, never silently committed).
    """

    def __init__(self, maxlen: int = 8):
        self._buf: "deque[Snapshot]" = deque(maxlen=maxlen)

    def add(self, snap: Snapshot) -> None:
        # The previous snapshot's validity ends when this screen entered.
        if self._buf:
            prev = self._buf[-1]
            if prev.next_transition is None:
                prev.next_transition = snap.t_start
        self._buf.append(snap)

    def _window_hi(self, snap: Snapshot) -> float:
        return snap.next_transition if snap.next_transition is not None else float("inf")

    def bind(self, t_tap: float, *, guard: float = 0.25) -> BindResult:
        matches = [s for s in self._buf if s.t_end <= t_tap < self._window_hi(s)]
        if not matches:
            return BindResult(None, "low")
        # newest matching window
        snap = max(matches, key=lambda s: s.t_end)
        confidence = "high"
        if len(matches) > 1:
            confidence = "low"
        else:
            # A transition boundary of the MATCHED window (its own edges) within
            # the guard interval makes the binding ambiguous (§5.1). Other
            # snapshots' boundaries are irrelevant once the window is fixed.
            boundaries = [snap.t_end]
            if snap.next_transition is not None:
                boundaries.append(snap.next_transition)
            if any(abs(t_tap - b) < guard for b in boundaries):
                confidence = "low"
        return BindResult(snap, confidence)
