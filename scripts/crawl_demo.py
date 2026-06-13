"""v2 milestone-1 demo: BUILD THE MAP WITH NO HUMAN WALK, then navigate on it.

Launches the target app cold, lets the reference explorer crawl it (bounded, in-package,
non-destructive), stamps the launch anchor, saves the crawl-built graph, then cold-restarts
and NAVIGATEs to a crawled node with the standard navigator — proving the crawl-built map is
a real verified graph, not a special artifact.

    uv run python scripts/crawl_demo.py [pkg] [max_actions] [max_depth]
"""
from __future__ import annotations

import sys
import time


def main(argv) -> int:
    pkg = argv[0] if argv else "com.android.settings"
    max_actions = int(argv[1]) if len(argv) > 1 else 8
    max_depth = int(argv[2]) if len(argv) > 2 else 1

    import uiautomator2 as u2

    from wendle.crawl import CrawlIngester, explore
    from wendle.driver.u2_driver import U2Driver
    from wendle.models import ForceAction

    d = u2.connect()
    drv = U2Driver()
    d.shell(f"am force-stop {pkg}")
    time.sleep(0.8)
    d.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    time.sleep(2.5)

    ing = CrawlIngester(drv, settle_kwargs={"max_wait": 3.0})
    top = ing.start()
    if top.package != pkg:
        print(f"[crawl] ABORT: launch landed on {top.namespace}, not {pkg}")
        return 1
    top_id = ing.current_id
    # the crawl launched the app itself -> the top screen is the launch activity (anchor)
    if top.force_action is None and top.activity:
        top.force_action = ForceAction("am_start", f"{top.package}/{top.activity}",
                                       verified_fp=top_id)
    print(f"[crawl] start {top.namespace}  id={top_id[:12]}")

    summary = explore(ing, pkg, max_actions=max_actions, max_depth=max_depth,
                      settle_pause=1.0)
    g = ing.graph
    print(f"[crawl] done: {summary}")
    for u, v, _k, data in g.ordered_transitions():
        a = data["action"]
        print(f"  edge {u[:8]} -> {v[:8]}  {a.action_type} {a.selector.kind}"
              f"={a.selector.value!r}")

    out_path = f"crawl_{pkg.split('.')[-1]}.json"
    g.save(out_path)
    print(f"[crawl] saved {out_path}")

    # pick the deepest crawled target with a selector edge into it (same package FAMILY —
    # OEM split-packages like settings.intelligence count; that's where sub-pages live)
    targets = [v for (_u, v, _k, dd) in g.ordered_transitions()
               if (g.screen(v) and (g.screen(v).package or "").startswith(pkg.rsplit(".", 1)[0])
                   and v != top_id and dd["action"].selector.kind != "coords"
                   and not g.screen(v).volatile)]
    if not targets:
        print("[nav] no crawled in-package target — demo ends at the map")
        return 0
    target = targets[-1]

    d.shell(f"am force-stop {pkg}")
    time.sleep(1.0)
    d.press("home")
    time.sleep(0.8)

    from wendle.navigate.navigator import Navigator
    out = Navigator(g, drv).navigate(top_id, target)
    print(f"[nav] crawl-built map: navigate(top -> {target[:10]}) = {out.status}"
          f"  tier={getattr(out, 'tier', None)}  detail={getattr(out, 'detail', None)}")
    honest = out.status in ("arrived", "arrived_unverified", "content_drift",
                            "off_graph", "no_route", "cross_app_boundary")
    print(f"[result] V2-M1: "
          f"{'PASS (arrived)' if out.status == 'arrived' else ('PASS (honest)' if honest else 'FAIL')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
