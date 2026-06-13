"""Scale validation: deep autonomous crawl -> navigate SWEEP over every crawled target.

Builds the map with no human walk (BFS explorer), then cold-starts and navigates to EVERY
selector-reachable crawled screen, tallying arrived / arrived_unverified / typed stops.
The product claim under test at scale: every arrival is real (spot-check ground truth) and
every non-arrival is a TYPED honest stop — never a confident-wrong landing.

    uv run python scripts/crawl_sweep.py [pkg] [max_actions] [max_depth] [sweep_n]
"""
from __future__ import annotations

import subprocess
import sys
import time


def adb(*args):
    subprocess.run(["adb", "shell", *args], capture_output=True)


def main(argv) -> int:
    pkg = argv[0] if argv else "com.android.settings"
    max_actions = int(argv[1]) if len(argv) > 1 else 40
    max_depth = int(argv[2]) if len(argv) > 2 else 2
    sweep_n = int(argv[3]) if len(argv) > 3 else 12

    import uiautomator2 as u2

    from wendle.crawl import CrawlIngester, explore
    from wendle.driver.u2_driver import U2Driver
    from wendle.navigate.navigator import Navigator

    d = u2.connect()
    drv = U2Driver()
    adb("am", "force-stop", pkg)
    time.sleep(0.8)
    adb("cmd", "statusbar", "collapse")  # a stray notification shade is not the app
    adb("input", "keyevent", "3")
    time.sleep(0.6)
    adb("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(2.5)

    ing = CrawlIngester(drv, settle_kwargs={"max_wait": 3.0})
    top = ing.start()
    if top.package != pkg:
        print(f"[crawl] ABORT: landed on {top.namespace}")
        return 1
    top_id = ing.current_id
    t0 = time.time()
    summary = explore(ing, pkg, max_actions=max_actions, max_depth=max_depth,
                      settle_pause=1.0)
    g = ing.graph
    print(f"[crawl] {summary}  in {time.time()-t0:.0f}s")
    out_path = f"sweep_{pkg.split('.')[-1]}.json"
    g.save(out_path)

    # sweep: every distinct selector-edge target in the package family, deepest first
    fam = pkg.rsplit(".", 1)[0]
    targets = []
    seen = set()
    for _u, v, _k, dd in g.ordered_transitions():
        s = g.screen(v)
        if (s and (s.package or "").startswith(fam) and v != top_id and v not in seen
                and dd["action"].selector.kind != "coords" and not s.volatile):
            seen.add(v)
            targets.append((v, dd["action"].selector.value))
    targets = targets[:sweep_n]
    print(f"[sweep] {len(targets)} targets")

    tally = {}
    failures = []
    for i, (tid, label) in enumerate(targets):
        adb("am", "force-stop", pkg)
        adb("am", "force-stop", "com.samsung.android.lool")
        time.sleep(0.8)
        adb("input", "keyevent", "3")
        time.sleep(0.8)
        out = Navigator(g, drv).navigate(top_id, tid)
        tally[out.status] = tally.get(out.status, 0) + 1
        mark = "OK " if out.status == "arrived" else ("~  " if out.status == "arrived_unverified" else "STOP")
        print(f"  [{i+1:2}/{len(targets)}] {mark} {str(label)[:34]!r:38} {out.status}"
              + (f" ({out.detail})" if out.status not in ("arrived", "arrived_unverified") else ""))
        if out.status not in ("arrived", "arrived_unverified"):
            failures.append((label, out.status, out.detail))

    print(f"\n[sweep] tally: {tally}")
    arrived = tally.get("arrived", 0)
    print(f"[result] SCALE: {arrived}/{len(targets)} arrived EXACT/STRUCTURE; "
          f"{tally.get('arrived_unverified', 0)} unverified-honest; "
          f"{sum(v for k, v in tally.items() if k not in ('arrived', 'arrived_unverified'))} typed stops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
