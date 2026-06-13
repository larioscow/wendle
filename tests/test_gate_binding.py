from wendle.capture.types import Snapshot
from wendle.gate.binding import bind_latest


def _snap(t_start, t_end):
    return Snapshot(t_start=t_start, t_end=t_end, hierarchy_hash="", nodes=[])


def _snaps():
    # three dumps completing at t_end 1.0, 2.0, 3.0 (sorted by t_end)
    return [_snap(0.6, 1.0), _snap(1.6, 2.0), _snap(2.6, 3.0)]


def test_binds_to_freshest_completed_dump():
    snap, conf = bind_latest(_snaps(), 2.3)
    assert snap.t_end == 2.0  # the dump that finished before 2.3, not 3.0
    assert conf == "high"


def test_no_prior_dump_is_none_low():
    snap, conf = bind_latest(_snaps(), 0.5)
    assert snap is None
    assert conf == "low"


def test_stale_dump_is_low_confidence():
    snap, conf = bind_latest(_snaps(), 9.0, stale_after=2.0)
    assert snap.t_end == 3.0
    assert conf == "low"  # 6s since the last dump


def test_empty_snaps_is_none_low():
    assert bind_latest([], 1.0) == (None, "low")


def test_picks_newest_not_oldest_prior():
    snap, _ = bind_latest(_snaps(), 3.5)
    assert snap.t_end == 3.0
