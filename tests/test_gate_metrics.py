import pytest

from wendle.gate.metrics import (
    GateThresholds,
    TapResult,
    compute_report,
    percentile,
)


def test_percentile_basic():
    assert percentile([], 50) == 0.0
    assert percentile([5], 95) == 5
    assert percentile([0, 10], 50) == 5.0
    assert percentile([0, 1, 2, 3, 4], 100) == 4
    assert percentile([0, 1, 2, 3, 4], 0) == 0


def test_percentile_rejects_out_of_range():
    with pytest.raises(ValueError):
        percentile([1, 2, 3], 150)
    with pytest.raises(ValueError):
        percentile([1, 2, 3], -1)


def _tap(replay, needs=False, bound=True):
    return TapResult(bound=bound, replayability=replay, needs_confirmation=needs)


def test_passing_run():
    taps = [_tap("high") for _ in range(19)] + [_tap("coordinate_only", needs=True)]
    report = compute_report(taps, [0.3] * 30, n_empty_dumps=0, n_total_dumps=30)
    assert report.passed is True
    assert report.recovery_rate == 0.95
    assert report.failures == []


def test_needs_confirmation_taps_do_not_count_as_recovered():
    # 16 confident-high + 4 high-but-LOW-confidence => true HIGH recovery 80% < 85% => FAIL
    taps = [_tap("high") for _ in range(16)] + [_tap("high", needs=True) for _ in range(4)]
    report = compute_report(taps, [0.3] * 30, 0, 30)
    assert report.recovery_rate == 0.8
    assert report.passed is False
    assert any("recovery" in f for f in report.failures)


def test_medium_resource_id_counts_as_recovery():
    # 20 resource_id (medium) selectors => recovery 100% (locale-robust, replayable),
    # but text/desc-only rate is 0%
    taps = [_tap("medium") for _ in range(20)]
    report = compute_report(taps, [0.3] * 30, 0, 30)
    assert report.recovery_rate == 1.0
    assert report.high_only_rate == 0.0
    assert report.passed is True


def test_fails_on_low_recovery():
    taps = [_tap("high") for _ in range(10)] + [_tap("coordinate_only") for _ in range(10)]
    report = compute_report(taps, [0.3] * 30, 0, 30)
    assert report.passed is False
    assert any("recovery" in f for f in report.failures)


def test_fails_on_high_dump_latency():
    taps = [_tap("high") for _ in range(20)]
    report = compute_report(taps, [0.3] * 15 + [3.0] * 5, 0, 20)
    assert report.passed is False
    assert any("p95" in f for f in report.failures)


def test_p95_exactly_at_threshold_fails():
    taps = [_tap("high") for _ in range(20)]
    report = compute_report(taps, [1.5] * 20, 0, 20)  # p95 == 1.5 must FAIL (< 1.5 required)
    assert report.passed is False
    assert any("p95" in f for f in report.failures)


def test_fails_on_high_empty_dump_rate():
    taps = [_tap("high") for _ in range(20)]
    report = compute_report(taps, [0.3] * 100, n_empty_dumps=100, n_total_dumps=100)
    assert report.passed is False
    assert any("empty-dump" in f for f in report.failures)


def test_fails_on_too_few_taps():
    report = compute_report([_tap("high")], [0.3] * 30, 0, 30)
    assert report.passed is False
    assert any("taps measured" in f for f in report.failures)


def test_fails_on_high_needs_confirmation():
    taps = [_tap("high", needs=True) for _ in range(10)] + [_tap("high") for _ in range(10)]
    report = compute_report(taps, [0.3] * 30, 0, 30)
    assert report.passed is False
    assert any("needs-confirmation" in f for f in report.failures)


def test_render_contains_result():
    report = compute_report([_tap("high") for _ in range(20)], [0.3] * 20, 0, 20)
    assert "PASS" in report.render()
