"""Realistic-anchor launch test (the way the framework is ACTUALLY used).

For each app: (1) open it the reliable launcher way (monkey -c LAUNCHER) and capture its REAL
foreground namespace — exactly the anchor a recording would stamp; (2) force-stop it cold; (3)
cold-launch via the LaunchLadder with THAT real anchor; (4) check the ladder LANDED (full-namespace
gate = reached the recorded activity). landed=False here = a Gemini-class app: am_start to the real
foreground activity fails cold and the recorded ICON TAP is genuinely needed (which this synthetic
graph lacks, so the ladder honestly can't reach it command-only).

    uv run python scripts/probe_launch_realistic.py [N]
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
    apps, seen = [], set()
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)$", line.strip())
        if not m:
            continue
        pkg = m.group(1)
        if pkg in EXCLUDE or pkg in seen:
            continue
        seen.add(pkg)
        apps.append(pkg)
    apps = apps[:n]
    print(f"Realistic-anchor test of {len(apps)} apps\n", flush=True)

    def fg():
        try:
            actv, win = driver.dumps()
            return foreground_namespace(actv, win) or "", focused_package(win) or ""
        except Exception:  # noqa: BLE001
            return "", ""

    def observe():
        ns, focus = fg()
        return ("<x/>", ns, focus, True)

    def wait_pkg(pkg, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            ns, focus = fg()
            if ns.split("/")[0] == pkg or focus == pkg:
                return ns
            time.sleep(0.3)
        return ""

    results = []
    for i, pkg in enumerate(apps, 1):
        # 1. learn the REAL foreground activity (what a recording would stamp)
        try:
            d.app_stop(pkg)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
        try:
            d.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        except Exception:  # noqa: BLE001
            pass
        real_ns = wait_pkg(pkg, 6.0)
        if not real_ns:
            results.append({"i": i, "pkg": pkg, "real_ns": "", "landed": None, "err": "couldnt_warm_open"})
            print(f"{i:2}/{len(apps)} ?? {pkg[:38]:38} could not warm-open (skip)", flush=True)
            d.press("home"); time.sleep(0.3)
            continue
        # 2. cold + 3. ladder-launch with the REAL anchor
        d.press("home"); time.sleep(0.3)
        try:
            d.app_stop(pkg)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
        g = Graph()
        g.upsert_screen(Screen(id="S", namespace=real_ns, package=pkg,
                               activity=real_ns.split("/", 1)[1] if "/" in real_ns else "",
                               force_action=ForceAction("am_start", real_ns, verified_fp="S")))
        ladder = LaunchLadder(g, driver, observe, activity_launch_timeout=3.0, launch_timeout=6.0)
        landed, err = False, ""
        try:
            res = ladder.launch(g.screen("S").force_action)
            landed, err = res.landed, (res.error or "")
        except Exception as e:  # noqa: BLE001
            err = "exc:" + repr(e)[:50]
        after_ns, _ = fg()
        results.append({"i": i, "pkg": pkg, "real_ns": real_ns, "landed": landed,
                        "err": err, "after": after_ns})
        print(f"{i:2}/{len(apps)} {'LAND' if landed else 'MISS'} {pkg[:34]:34} "
              f"anchor={real_ns.split('/')[-1][:24]:24} {'' if landed else 'err='+err+' after='+after_ns[:30]}",
              flush=True)
        try:
            d.app_stop(pkg)
        except Exception:  # noqa: BLE001
            pass
        d.press("home"); time.sleep(0.3)

    tested = [r for r in results if r["landed"] is not None]
    landed_n = sum(1 for r in tested if r["landed"])
    print(f"\n==== REALISTIC SUMMARY: tested={len(tested)} | LANDED={landed_n} | "
          f"couldnt-warm-open={len(results) - len(tested)} ====", flush=True)
    print("\nGEMINI-CLASS (real foreground activity not cold-am_start-able; needs the recorded icon tap):")
    for r in tested:
        if not r["landed"]:
            print(f"  {r['pkg']} | anchor={r['real_ns']} | err={r['err']} | after={r['after']}")
    json.dump(results, open("launch_realistic_results.json", "w"), indent=1)
    print("\nresults -> launch_realistic_results.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 50))
