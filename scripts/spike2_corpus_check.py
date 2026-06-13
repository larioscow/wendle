"""Spike 2 corpus checker — offline fingerprint validation over a captured corpus.

    uv run python scripts/spike2_corpus_check.py [corpus_dir]

Re-fingerprints every saved dump with the CURRENT code (so the corpus is a
regression test, not a snapshot of capture-time behavior) and checks:

  • STABILITY (automatic): a sample's re-dumps must yield ONE fingerprint.
  • UNDER-MERGE (needs labels): samples sharing a `label` must share a fingerprint.
  • OVER-MERGE  (needs labels): samples with DIFFERENT labels must NOT share one.

Label samples by opening each sample's screen.png and setting `label` in its
meta.json (e.g. "settings_home", "wifi_list"). Exits non-zero on any violation.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def main(corpus_dir: str = "corpus") -> int:
    from wendle.fingerprint.compose import resolve_profile
    from wendle.fingerprint.signature import fingerprint

    root = Path(corpus_dir)
    if not root.exists():
        print(f"No corpus at {root}/ — run spike2_capture_corpus.py first.")
        return 1

    samples = sorted(p for p in root.glob("sample_*") if p.is_dir())
    if not samples:
        print(f"No samples in {root}/.")
        return 1

    rows: List[dict] = []
    unstable: List[str] = []
    for s in samples:
        meta = json.loads((s / "meta.json").read_text())
        ns = meta.get("namespace", "unknown")
        focus = meta.get("focus")
        label = (meta.get("label") or "").strip()
        dumps = [x.read_text() for x in sorted(s.glob("dump_*.xml"))]
        # decide the profile ONCE (on the first dump), launcher-aware, like the harness
        cfg = resolve_profile(dumps[0], ns) if dumps else None
        fps = [fingerprint(ns, x, cfg, focus_pkg=focus) for x in dumps]
        stable = len(set(fps)) == 1
        if not stable:
            unstable.append(s.name)
        rows.append({"dir": s.name, "ns": ns, "label": label, "fp": fps[0], "stable": stable})

    n = len(rows)
    distinct_fp = len({r["fp"] for r in rows})
    n_compose = sum(1 for s in samples if json.loads((s / "meta.json").read_text()).get("compose"))
    n_labeled = sum(1 for r in rows if r["label"])

    print("==== Spike 2 corpus check ====")
    print(f"samples ............ {n}")
    print(f"distinct fingerprints {distinct_fp}")
    print(f"compose samples .... {n_compose}")
    print(f"labeled samples .... {n_labeled}/{n}")

    violations = 0

    # STABILITY (automatic)
    if unstable:
        violations += len(unstable)
        print(f"\nUNSTABLE samples ({len(unstable)}) — re-dumps disagreed (volatile / under-merge):")
        for name in unstable:
            print(f"  - {name}")
    else:
        print("\nstability .......... OK (every sample's re-dumps agree)")

    if n_labeled:
        # UNDER-MERGE: same label must share one fingerprint
        by_label: Dict[str, set] = defaultdict(set)
        for r in rows:
            if r["label"]:
                by_label[r["label"]].add(r["fp"])
        under = {lbl: fps for lbl, fps in by_label.items() if len(fps) > 1}
        if under:
            violations += len(under)
            print(f"\nUNDER-MERGE ({len(under)}) — same screen, different ids:")
            for lbl, fps in under.items():
                print(f"  - '{lbl}' -> {len(fps)} different fingerprints")
        else:
            print("under-merge ........ OK (each label has one fingerprint)")

        # OVER-MERGE: different labels must not share a fingerprint
        fp_to_labels: Dict[str, set] = defaultdict(set)
        for r in rows:
            if r["label"]:
                fp_to_labels[r["fp"]].add(r["label"])
        over = {fp: lbls for fp, lbls in fp_to_labels.items() if len(lbls) > 1}
        if over:
            violations += len(over)
            print(f"\nOVER-MERGE ({len(over)}) — different screens, same id:")
            for fp, lbls in over.items():
                print(f"  - {fp} <- {sorted(lbls)}")
        else:
            print("over-merge ......... OK (distinct labels have distinct ids)")
    else:
        print("\n(no labels set — only stability checked. Label samples in meta.json")
        print(" to enable the over/under-merge checks.)")

    print("==============================")
    if violations:
        print(f"FAIL — {violations} violation group(s).")
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "corpus"))
