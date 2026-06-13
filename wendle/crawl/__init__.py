"""v2 milestone 1 — the crawl-INGESTION back-end (the moat side of the v2 split).

The owner's decision: ADOPT an external crawl front-end (DroidRun / mobile-mcp / DroidBot
class — they pick actions); we own the honesty-gated back-end that turns their actuations
into the verified graph. `CrawlIngester` is that back-end: observe-after through the shared
settle discipline, mint/commit through the ONE GraphBuilder, full geometry via BindContext —
so a crawl-built graph is byte-identical to a hand-recorded one (the convergence lock).

`explore()` is a deliberately MINIMAL reference front-end (systematic, bounded, in-package,
non-destructive) proving the seam end-to-end without a human walk. It is NOT the product —
replace it with a real explorer; the graph stays honest by construction either way."""
from wendle.crawl.ingester import CrawlIngester
from wendle.crawl.explorer import explore

__all__ = ["CrawlIngester", "explore"]
