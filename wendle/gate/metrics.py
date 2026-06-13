from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List


@dataclass
class TapResult:
    """Outcome of one recorded tap during a gate run.

    Recovery is derived from `replayability` + `needs_confirmation` in
    `compute_report`. A tap counts as recovered when it got ANY stable selector
    (`high` text/content-desc OR `medium` resource-id — all replayable; the §8
    ladder minus coordinates) AND the binding was confident
    (`needs_confirmation=False`). `resource-id` is locale-robust, so it counts.
    """

    bound: bool  # a snapshot was found to correlate against (§5.1)
    replayability: str  # high / medium / coordinate_only / none
    needs_confirmation: bool


@dataclass
class GateThresholds:
    """Pass/fail bar for the Spike 1 kill gate (§14). Set BEFORE measuring."""

    min_recovery_rate: float = 0.85  # HIGH-confidence selector recovery
    max_needs_confirmation_rate: float = 0.25
    max_p95_dump_latency_s: float = 1.5
    max_empty_dump_rate: float = 0.05
    min_taps: int = 20


@dataclass
class GateReport:
    n_taps: int
    recovery_rate: float  # any stable selector (text/desc/resource-id), gated
    high_only_rate: float  # informational: text/content-desc only
    needs_confirmation_rate: float
    coordinate_only_rate: float
    dump_p50: float
    dump_p95: float
    dump_p99: float
    empty_dump_rate: float
    passed: bool
    failures: List[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"taps measured ............ {self.n_taps}",
            f"selector recovery ........ {self.recovery_rate:.1%}",
            f"  (text/desc only) ....... {self.high_only_rate:.1%}",
            f"needs-confirmation ....... {self.needs_confirmation_rate:.1%}",
            f"coordinate-only .......... {self.coordinate_only_rate:.1%}",
            f"dump latency p50/p95/p99 . {self.dump_p50:.2f}s / {self.dump_p95:.2f}s / {self.dump_p99:.2f}s",
            f"empty-dump rate .......... {self.empty_dump_rate:.1%}",
            f"RESULT ................... {'PASS' if self.passed else 'FAIL'}",
        ]
        for f in self.failures:
            lines.append(f"  - {f}")
        return "\n".join(lines)


def percentile(values: List[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0, 100]); 0.0 for empty input."""
    if not values:
        return 0.0
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"percentile q must be in [0, 100], got {q}")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (q / 100.0) * (len(s) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return s[lo]
    frac = rank - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def compute_report(
    taps: List[TapResult],
    dump_latencies: List[float],
    n_empty_dumps: int,
    n_total_dumps: int,
    thresholds: GateThresholds = GateThresholds(),
) -> GateReport:
    """Aggregate per-tap results + dump timings into a pass/fail gate report.

    Recovery = ANY stable selector (text/content-desc OR resource-id) with a
    confident binding. resource-id is locale-robust and fully replayable, so it
    counts. text/content-desc-only is reported separately as `high_only_rate`.
    A LOW-confidence binding never counts as recovered.
    """
    n = len(taps)
    failures: List[str] = []

    if n < thresholds.min_taps:
        failures.append(f"only {n} taps measured (need >= {thresholds.min_taps})")

    def confident(t: TapResult) -> bool:
        return not t.needs_confirmation

    high = sum(1 for t in taps if t.replayability == "high" and confident(t))
    recovered = sum(1 for t in taps if t.replayability in ("high", "medium") and confident(t))
    recovery_rate = (recovered / n) if n else 0.0
    high_only_rate = (high / n) if n else 0.0
    needs_rate = (sum(1 for t in taps if t.needs_confirmation) / n) if n else 0.0
    coord_rate = (sum(1 for t in taps if t.replayability == "coordinate_only") / n) if n else 0.0
    p50 = percentile(dump_latencies, 50)
    p95 = percentile(dump_latencies, 95)
    p99 = percentile(dump_latencies, 99)
    empty_rate = (n_empty_dumps / n_total_dumps) if n_total_dumps else 0.0

    if n and recovery_rate < thresholds.min_recovery_rate:
        failures.append(
            f"selector recovery {recovery_rate:.1%} < {thresholds.min_recovery_rate:.0%}"
        )
    if n and needs_rate > thresholds.max_needs_confirmation_rate:
        failures.append(
            f"needs-confirmation {needs_rate:.1%} > {thresholds.max_needs_confirmation_rate:.0%}"
        )
    if dump_latencies and p95 >= thresholds.max_p95_dump_latency_s:
        failures.append(
            f"dump p95 {p95:.2f}s >= {thresholds.max_p95_dump_latency_s:.2f}s"
        )
    if n_total_dumps and empty_rate > thresholds.max_empty_dump_rate:
        failures.append(
            f"empty-dump rate {empty_rate:.1%} > {thresholds.max_empty_dump_rate:.0%}"
        )

    return GateReport(
        n_taps=n,
        recovery_rate=recovery_rate,
        high_only_rate=high_only_rate,
        needs_confirmation_rate=needs_rate,
        coordinate_only_rate=coord_rate,
        dump_p50=p50,
        dump_p95=p95,
        dump_p99=p99,
        empty_dump_rate=empty_rate,
        passed=len(failures) == 0,
        failures=failures,
    )
