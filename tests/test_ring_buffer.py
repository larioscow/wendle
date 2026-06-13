from wendle.capture.ring_buffer import SnapshotRingBuffer
from wendle.capture.types import Snapshot


def _snap(t_start, t_end, h):
    return Snapshot(t_start=t_start, t_end=t_end, hierarchy_hash=h, nodes=[])


def test_binds_tap_to_containing_window_high_confidence():
    buf = SnapshotRingBuffer()
    buf.add(_snap(0.0, 0.2, "A"))
    buf.add(_snap(3.0, 3.2, "B"))  # closes A's window at 3.0
    res = buf.bind(1.5)  # well inside A's [0.2, 3.0), far from boundaries
    assert res.snapshot.hierarchy_hash == "A"
    assert res.confidence == "high"


def test_tap_near_transition_boundary_is_low_confidence():
    buf = SnapshotRingBuffer()
    buf.add(_snap(0.0, 0.2, "A"))
    buf.add(_snap(3.0, 3.2, "B"))
    res = buf.bind(2.95)  # within 0.25s guard of the 3.0 boundary
    assert res.snapshot.hierarchy_hash == "A"
    assert res.confidence == "low"


def test_tap_with_no_settled_window_is_low_and_unbound():
    buf = SnapshotRingBuffer()
    buf.add(_snap(0.0, 0.2, "A"))
    res = buf.bind(0.1)  # before t_end of the only snapshot
    assert res.snapshot is None
    assert res.confidence == "low"


def test_binds_to_newest_current_window():
    buf = SnapshotRingBuffer()
    buf.add(_snap(0.0, 0.2, "A"))
    buf.add(_snap(3.0, 3.2, "B"))  # open-ended current window
    res = buf.bind(10.0)
    assert res.snapshot.hierarchy_hash == "B"
    assert res.confidence == "high"


def test_bind_on_empty_buffer_is_none_low():
    res = SnapshotRingBuffer().bind(1.0)
    assert res.snapshot is None
    assert res.confidence == "low"
