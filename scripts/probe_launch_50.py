"""Stress-test the launch mechanism across N real apps on the device.

For each launchable app: force-stop it (cold), launch it through the framework's LaunchLadder
(with a synthetic am_start anchor = its launcher activity), and confirm the app's PACKAGE
actually foregrounds. PASS = package opened. The ladder's strict full-namespace verdict is
recorded separately for diagnosis (a synthetic launcher-activity anchor mismatches an app that
routes to a different home activity — that is a TEST artifact, not a launch failure; the
package-level check is the honest "did it open").

    uv run python scripts/probe_launch_50.py [N]
"""
from __future__ import annotations

import json
import re
import sys
import time

import uiautomator2 as u2

from wendle.driver.u2_driver import U2Driver
from wendle.fingerprint.dumpsys import focused_package, foreground_namespace
from wendle.graph import Graph
from wendle.launch import LaunchLadder
from wendle.models import ForceAction, Screen

# never force-stop the launcher (need HOME), the u2 agent/IME (need them), or pure system surfaces.
EXCLUDE = {
    "com.sec.android.app.launcher", "com.github.uiautomator", "android", "com.android.systemui",
    "com.google.android.inputmethod.latin", "com.samsung.android.honeyboard", "com.google.android.tts",
    "com.samsung.android.app.cocktailbarservice", "com.samsung.android.mtpapplication",
}


def main(n: int = 50) -> int:
    d = u2.connect()
    driver = U2Driver(device=d)

    out = d.shell("cmd package query-activities -a android.intent.action.MAIN "
                  "-c android.intent.category.LAUNCHER --components").output
    apps = []
    seen = set()
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)$", line.strip())
        if not m:
            continue
        pkg, act = m.group(1), m.group(2)
        if pkg in EXCLUDE or pkg in seen:
            continue
        seen.add(pkg)
        apps.append((pkg, act))
    apps = apps[:n]
    print(f"Testing {len(apps)} apps (force-stop -> ladder-launch -> confirm foreground)\n", flush=True)

    def fg():
        try:
            actv, win = driver.dumps()
            return foreground_namespace(actv, win) or "", focused_package(win) or ""
        except Exception:  # noqa: BLE001
            return "", ""

    def observe():
        ns, focus = fg()
        return ("<x/>", ns, focus, True)

    results = []
    for i, (pkg, act) in enumerate(apps, 1):
        full = pkg + "/" + act
        g = Graph()
        g.upsert_screen(Screen(id="S", namespace=full, package=pkg, activity=act,
                               force_action=ForceAction("am_start", full, verified_fp="S")))
        ladder = LaunchLadder(g, driver, observe, activity_launch_timeout=3.0, launch_timeout=6.0)
        try:
            d.app_stop(pkg)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
        t0 = time.time()
        landed, err = False, ""
        try:
            res = ladder.launch(g.screen("S").force_action)
            landed, err = res.landed, (res.error or "")
        except Exception as e:  # noqa: BLE001
            err = "exc:" + repr(e)[:50]
        dt = time.time() - t0
        time.sleep(0.4)
        ns_after, focus_after = fg()
        opened = ns_after.split("/")[0] == pkg or focus_after == pkg
        results.append({"i": i, "pkg": pkg, "act": act, "landed": landed, "err": err,
                        "opened": opened, "after": ns_after, "dt": round(dt, 1)})
        verdict = "land" if landed else (err[:20] or "no")
        print(f"{i:2}/{len(apps)} {'OK' if opened else 'XX'} {pkg[:38]:38} "
              f"ladder={verdict:20} after={ns_after[:40]:40} {dt:.1f}s", flush=True)
        try:
            d.app_stop(pkg)
        except Exception:  # noqa: BLE001
            pass
        d.press("home")
        time.sleep(0.3)

    opened_n = sum(1 for r in results if r["opened"])
    landed_n = sum(1 for r in results if r["landed"])
    print(f"\n==== SUMMARY: {len(results)} apps | OPENED={opened_n} | ladder-landed={landed_n} ====", flush=True)
    print("\nDID NOT OPEN (genuine launch failures):")
    for r in results:
        if not r["opened"]:
            print(f"  {r['pkg']} | act={r['act']} | err={r['err']} | after={r['after']}")
    print("\nOPENED but ladder NOT-landed (synthetic-anchor gate strictness; not a real-recording failure):")
    for r in results:
        if r["opened"] and not r["landed"]:
            print(f"  {r['pkg']} | err={r['err']} | after={r['after']}")
    json.dump(results, open("launch_50_results.json", "w"), indent=1)
    print("\nresults -> launch_50_results.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 50))
