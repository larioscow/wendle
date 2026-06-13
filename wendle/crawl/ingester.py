"""CrawlIngester — the external-explorer front door onto the GraphBuilder.

Contract (the recommended bounds-supplying integration, convergence-locked):
the EXPLORER actuates the device however it likes (its own driver calls), then reports the
Action it took plus the actuated element's bounds; the ingester observes-after through the
SHARED settle discipline (record/observe.py) and commits through the ONE minter/commit path
(record/builder.py). All honesty gates are the builder's — structural, not opt-in:
unsettled observations mint only volatile nodes, source volatility is recomputed, the §2.8
tripwire runs coordinate-free, stage_pending rejects sensitive literals, and a geometry-less
report degrades honestly (probe / navigate-fallback, never a guessed reveal)."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from wendle.models import Action, Screen, Transition
from wendle.record.builder import BindContext, GraphBuilder, EnterResult
from wendle.record.observe import observe_settled


class CrawlIngester:
    def __init__(self, driver, *, sink: Optional[Callable[[dict], None]] = None,
                 settle_kwargs: Optional[dict] = None,
                 dump_lock: Optional[threading.Lock] = None):
        self.driver = driver
        self.lock = dump_lock or threading.Lock()
        self.settle_kwargs = settle_kwargs or {}
        self.builder = GraphBuilder(sink=sink, lock=self.lock)
        self.last_entered: Optional[EnterResult] = None

    @property
    def graph(self):
        return self.builder.graph

    @property
    def current_id(self):
        return self.builder.current_id

    def observe(self) -> EnterResult:
        """Settle + mint the live screen (no commit) — the explorer reads the snapshot from
        the returned EnterResult (also kept as `last_entered`) to pick its next action."""
        xml, ns, settled, focus = observe_settled(self.driver, self.lock, **self.settle_kwargs)
        self.last_entered = self.builder.enter(xml, ns, settled, focus)
        return self.last_entered

    def start(self) -> Screen:
        """Anchor the crawl on the current screen."""
        return self.builder.begin(self.observe())

    def commit(self, action: Action, *, bounds=None, px=None, py=None, end=None,
               landed: bool = True, needs_confirmation: bool = False) -> Optional[Transition]:
        """The explorer ALREADY actuated `action`; observe the outcome and commit it.
        Supply the actuated element's bounds (+ tap point / swipe end) for byte-identical
        classification with the human recorder; omitting geometry degrades honestly."""
        after = self.observe()
        if px is None and bounds is not None:
            px, py = (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
        return self.builder.commit_transition(
            action=action, after=after,
            bind=BindContext(px=px, py=py, end=end, bounds=bounds, landed=landed),
            needs_confirmation=needs_confirmation)

    def reposition(self) -> Screen:
        """Re-anchor `current` on whatever is live WITHOUT minting an edge — for explorer
        moves that are not recorded interactions (BACK retreats, package-guard escapes).
        Honest by construction: an untracked move never fabricates a transition."""
        return self.builder.begin(self.observe())
