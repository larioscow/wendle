"""On-device validation of task #17b twin identity (Galaxy S23, Samsung One UI).

Records a visit to TWO sibling com.android.settings/.SubSettings pages (which collapse to ONE
text-free structure_id) and proves the recorder REFINES them into distinct nodes on the observed
chrome collision — the whole point of #17b. Without #17b they would share one node.

ACTUATES with uiautomator2 (real InputManager touches) and feeds the REAL RecordSession its
Gesture seam (the auto_record.py substrate). Pinned to the S23 by serial — NEVER the emulator.

    ANDROID_SERIAL=192.168.1.192:45307 uv run python scripts/twin_validate.py [labelA] [labelB]
"""
from __future__ import annotations

import os
import sys
import time

REQUIRED_MODEL = "918"  # SM-S918U (Galaxy S23). Abort on any other device (emulator off-limits).


def main(argv) -> int:
    serial = os.environ.get("ANDROID_SERIAL")
    if not serial:
        print("set ANDROID_SERIAL to the S23 (e.g. 192.168.1.192:45307)"); return 2
    labelA = argv[0] if len(argv) > 0 else "Conexiones"
    labelB = argv[1] if len(argv) > 1 else "Dispositivos conectados"

    from scripts.auto_record import AutoRecorder
    rec = AutoRecorder(settle_wait=3.0, settle_after_tap=1.5)
    model = rec.d.device_info.get("model", "")
    if REQUIRED_MODEL not in str(model):
        print(f"ABORT: connected device is {model!r}, not the S23 — refusing to instrument it")
        return 3
    print(f"[dev] {model}  serial={rec.d.serial}")

    # deterministic: open Settings FRESH at its homepage
    rec.d.shell("am force-stop com.android.settings")
    rec.d.shell("am start -a android.settings.SETTINGS")
    time.sleep(2.0)

    def _title():
        # the .SubSettings toolbar title (what the chrome digest distinguishes) — for a
        # human-verifiable, named result. Best-effort.
        for rid in ("com.android.settings:id/collapsing_toolbar", "android:id/title"):
            el = rec.d(resourceId=rid)
            if el.exists and el.info.get("contentDescription"):
                return el.info["contentDescription"]
        return rec.d.app_current().get("activity")

    def _home():
        # deterministic return to the homepage (back can stay within a sub-page on One UI)
        for _ in range(4):
            if "Homepage" in rec.d.app_current().get("activity", ""):
                return
            rec.d.press("back"); time.sleep(1.0)

    rec.start()                                   # anchor on the Settings homepage
    rec.step(("tap_text", labelA))                # home -> .SubSettings (twin A): mints coarse F
    print(f"[#17b] twin A title: {_title()!r}")
    _home(); rec.session._reconcile_current_screen()
    rec.step(("tap_text", labelB))                # home -> .SubSettings (twin B): COLLISION -> SPLIT
    print(f"[#17b] twin B title: {_title()!r}")

    g = rec.session.graph
    g.save("twin_validate.json")
    print(f"\n[rec] {g.g.number_of_nodes()} screens, {g.g.number_of_edges()} edges")

    # ---- the validation: did the two .SubSettings siblings refine apart? ----
    sub = [n for n in g.g.nodes if g.screen(n).namespace.endswith(".SubSettings")]
    print(f"\n[#17b] .SubSettings nodes: {len(sub)}")
    by_struct = {}
    for n in sub:
        s = g.screen(n)
        by_struct.setdefault(s.structure_id, []).append(s)
    ok = False
    for struct, screens in by_struct.items():
        refined = [s for s in screens if s.coarse_id is not None]
        print(f"  structure_id={struct[:12]}  nodes={len(screens)}  refined_twins={len(refined)}")
        for s in screens:
            print(f"    id={s.id[:14]}  coarse_id={(s.coarse_id or '-')[:12]}  "
                  f"digest={(s.chrome_digest or '-')[:10]}  adapter_dominant={s.adapter_dominant}")
        if len(refined) >= 2 and len({s.id for s in refined}) >= 2:
            ok = True
    print("\n[#17b] RESULT:",
          "PASS — the two sibling .SubSettings pages refined into DISTINCT nodes (chrome collision split)"
          if ok else
          "NOT SPLIT — the siblings did not refine (check the labels both open .SubSettings; see nodes above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
