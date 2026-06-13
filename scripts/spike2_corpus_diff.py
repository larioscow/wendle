"""Spike 2 corpus diagnosis — show WHY samples are unstable / colliding.

    uv run python scripts/spike2_corpus_diff.py [corpus_dir]

For each unstable sample, prints the (class, resource-id) nodes that appear or
disappear between its re-dumps (the churning elements). For each fingerprint
shared by >1 sample, prints the samples + namespaces (to spot namespace
mis-resolution / near-empty collisions). Pure offline analysis over saved dumps.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Set, Tuple

from defusedxml.ElementTree import fromstring as _xml


def _node_keys(xml: str) -> Set[Tuple[str, str]]:
    try:
        root = _xml(xml)
    except Exception:  # noqa: BLE001
        return set()
    return {(el.get("class", ""), el.get("resource-id", "")) for el in root.iter("node")}


def main(corpus_dir: str = "corpus") -> int:
    from wendle.fingerprint.compose import select_config
    from wendle.fingerprint.signature import fingerprint

    root = Path(corpus_dir)
    samples = sorted(p for p in root.glob("sample_*") if p.is_dir())
    if not samples:
        print(f"No samples in {root}/.")
        return 1

    fp_to_samples = defaultdict(list)

    print("==== unstable-sample churn (what changes between re-dumps) ====")
    for s in samples:
        meta = json.loads((s / "meta.json").read_text())
        ns = meta.get("namespace", "unknown")
        label = meta.get("label", "")
        dumps = [p.read_text() for p in sorted(s.glob("dump_*.xml"))]
        fps = [fingerprint(ns, x, select_config(x)) for x in dumps]
        fp_to_samples[fps[0]].append((s.name, ns, label))
        if len(set(fps)) == 1:
            continue
        keys0, keysN = _node_keys(dumps[0]), _node_keys(dumps[-1])
        appeared = keysN - keys0
        vanished = keys0 - keysN
        print(f"\n{s.name}  label={label!r} ns={ns}")
        print(f"  nodes dump0={len(keys0)} dumpN={len(keysN)}  fps={[f[:8] for f in fps]}")
        for cls, rid in sorted(appeared)[:8]:
            print(f"    + {rid or '(no id)':45s} {cls.split('.')[-1]}")
        for cls, rid in sorted(vanished)[:8]:
            print(f"    - {rid or '(no id)':45s} {cls.split('.')[-1]}")

    print("\n==== fingerprints shared by >1 sample (over-merge suspects) ====")
    for fp, rows in fp_to_samples.items():
        if len(rows) > 1:
            print(f"\n{fp}")
            for name, ns, label in rows:
                print(f"    label={label!r:20s} ns={ns}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "corpus"))
