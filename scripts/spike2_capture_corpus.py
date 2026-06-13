"""Spike 2 corpus capture — record a labeled fingerprint test corpus on-device.

    uv run python scripts/spike2_capture_corpus.py [seconds] [out_dir]

Navigate BROADLY across many apps (>= 10, including a modern Compose app, a
browser/WebView, a chat app, Settings multi-level). Each time the foreground
screen SETTLES on a new state, a sample is saved: 3 re-dumps (to measure
stability), a screenshot (to label later), and metadata. Builds real test data
instead of a one-off eyeball check.

Output (gitignored — contains device PII):
    corpus/sample_NN_<ns>/dump_0.xml dump_1.xml dump_2.xml screen.png meta.json
    corpus/manifest.json   (one row per sample; add a "label" to each for the
                            over/under-merge checks in spike2_corpus_check.py)
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import List, Optional


def _shell(d, cmd: str) -> str:
    r = d.shell(cmd)
    out = getattr(r, "output", None)
    if out is not None:
        return out
    return r[0] if isinstance(r, tuple) else str(r)


def _safe(ns: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ns)[:48]


def main(duration: float = 90.0, out_dir: str = "corpus") -> int:
    try:
        import uiautomator2 as u2
    except Exception as e:  # noqa: BLE001
        print("Run via `uv run`. Import error:", e)
        return 1

    from wendle.fingerprint.compose import (
        VIEW_PROFILE,
        is_compose_dominant,
        resolve_profile,
    )
    from wendle.fingerprint.dumpsys import focused_package, foreground_namespace
    from wendle.fingerprint.settle import settle
    from wendle.fingerprint.signature import fingerprint

    def _ns() -> str:
        try:
            return foreground_namespace(
                _shell(d, "dumpsys activity activities"), _shell(d, "dumpsys window")
            )
        except Exception:  # noqa: BLE001
            return "unknown"

    def _focus() -> str:
        try:
            return focused_package(_shell(d, "dumpsys window"))
        except Exception:  # noqa: BLE001
            return None

    # stillness check uses a STABLE profile (no per-dump Compose flicker)
    _stable_cfg = lambda _x: VIEW_PROFILE  # noqa: E731

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("Connecting...")
    d = u2.connect()
    print(f"  {d.info.get('productName')} {d.info.get('displayWidth')}x{d.info.get('displayHeight')}")
    print(f"\nNavigate broadly across many apps for {int(duration)}s. Pause on each screen.\n")

    manifest: List[dict] = []
    last_fp: Optional[str] = None
    n = 0
    deadline = time.time() + duration

    while time.time() < deadline:
        try:
            # Wait for the screen to truly SETTLE (3 consecutive identical sanitized
            # dumps + stable namespace) — never capture mid-transition / mid-load.
            xml1, ns, settled = settle(
                d.dump_hierarchy, _ns, _stable_cfg, focus_fn=_focus, need=3, max_wait=5.0
            )
        except Exception as e:  # noqa: BLE001
            print("  (capture error, skipping):", e)
            time.sleep(1.0)
            continue
        if not xml1.strip():
            time.sleep(1.0)
            continue
        if not settled:
            time.sleep(1.0)  # genuinely volatile/live screen — skip (namespace-dominant, Spike 3)
            continue

        focus = _focus()
        cfg = resolve_profile(xml1, ns)  # decide the profile ONCE (launcher-aware)
        fp = fingerprint(ns, xml1, cfg, focus_pkg=focus)
        if fp == last_fp:
            time.sleep(1.0)
            continue
        last_fp = fp

        # capture a sample: FAST back-to-back re-dumps to measure true fingerprint
        # determinism on the held screen (slow re-dumps just catch you navigating).
        dumps = [xml1]
        for _ in range(2):
            time.sleep(0.12)
            try:
                dumps.append(d.dump_hierarchy())
            except Exception:  # noqa: BLE001
                dumps.append(xml1)
        fps = [fingerprint(ns, x, cfg, focus_pkg=focus) for x in dumps]
        stable = len(set(fps)) == 1
        compose = is_compose_dominant(xml1)

        sample_dir = out / f"sample_{n:03d}_{_safe(ns)}"
        sample_dir.mkdir(parents=True, exist_ok=True)
        for i, x in enumerate(dumps):
            (sample_dir / f"dump_{i}.xml").write_text(x)
        try:
            d.screenshot(str(sample_dir / "screen.png"))
        except Exception:  # noqa: BLE001
            pass
        meta = {
            "sample": n,
            "namespace": ns,
            "focus": focus,
            "fingerprints": fps,
            "stable": stable,
            "compose": compose,
            "n_nodes": xml1.count("<node"),
            "label": "",  # <-- fill in from the screenshot for over/under-merge checks
        }
        (sample_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        manifest.append({"dir": sample_dir.name, **meta})

        flag = "" if stable else "  UNSTABLE(re-dumps disagreed)"
        print(f"  sample {n:03d}  {fps[0]}  compose={'Y' if compose else 'N'} ns={_safe(ns)}{flag}")
        n += 1
        time.sleep(1.2)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nSaved {n} samples to {out}/  (+ manifest.json)")
    print("Next: label each sample (open screen.png, set 'label' in meta.json/manifest.json),")
    print("      then run:  uv run python scripts/spike2_corpus_check.py")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    nums = [a for a in args if a.replace(".", "", 1).isdigit()]
    dirs = [a for a in args if a not in nums]
    dur = float(nums[0]) if nums else 90.0
    odir = dirs[0] if dirs else "corpus"
    sys.exit(main(dur, odir))
