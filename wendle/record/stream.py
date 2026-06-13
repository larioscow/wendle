from __future__ import annotations

from typing import Iterable, Iterator

from wendle.capture.events import parse_getevent_stream
from wendle.capture.protocols.base import TouchProtocol
from wendle.capture.types import Gesture


def stream_gestures(
    lines: Iterable[str],
    protocol: TouchProtocol,
    *,
    swipe_dist: int = 30,
    x_scale: float = 1.0,
    y_scale: float = 1.0,
) -> Iterator[Gesture]:
    """Turn a live getevent line stream into finalized Gestures incrementally.

    Re-feed-and-diff: the batch protocol decoders keep their state in `decode`'s
    locals and cannot be fed one event at a time, so we accumulate InputEvents and
    re-decode the full buffer on each complete line, yielding only newly-finalized
    gestures. A gesture is final once a *later* event proves its contact closed —
    decode() flushes a still-open contact as `truncated=True` at end-of-buffer, so
    a trailing truncated gesture is held back until it either closes or the stream
    ends. At end-of-stream the trailing gesture IS real and is flushed (parity with
    segment_gestures over the same blob).

    `lines` must yield COMPLETE lines (a partial readline chunk would silently drop
    via the anchored ^...$ regex); the caller buffers raw bytes to newlines.
    """
    events = []
    emitted = 0
    for line in lines:
        evs = parse_getevent_stream(line)
        if not evs:
            continue
        events.extend(evs)
        gestures = protocol.decode(events, swipe_dist=swipe_dist, x_scale=x_scale, y_scale=y_scale)
        final = gestures[:-1] if (gestures and gestures[-1].truncated) else gestures
        yield from final[emitted:]
        emitted = len(final)
    # end of stream: flush whatever remains, including a trailing truncated gesture
    gestures = protocol.decode(events)
    yield from gestures[emitted:]
