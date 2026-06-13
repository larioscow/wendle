"""Spike 2 on-device check — screen-fingerprint stability (§14).

    uv run python scripts/spike2_verify.py [seconds]

Navigate deliberately: A -> B -> C, then BACK to A, then to B again. Each time the
foreground screen changes, a line prints with its fingerprint. The acceptance:
  - revisiting a screen prints the SAME id  -> no under-merge
  - genuinely different screens get DIFFERENT ids -> no over-merge (you judge this)
  - a screen whose two quick dumps disagree is flagged `volatile` (animation).

Nothing is recorded to disk.
"""

from __future__ import annotations

import sys
import time
from typing import Dict, List, Optional


def _shell(d, cmd: str) -> str:
    r = d.shell(cmd)
    out = getattr(r, "output", None)
    if out is not None:
        return out
    if isinstance(r, tuple):
        return r[0]
    return str(r)


def main(duration: float = 45.0) -> int:
    try:
        import uiautomator2 as u2
    except Exception as e:  # noqa: BLE001
        print("Run via `uv run`. Import error:", e)
        return 1

    from wendle.fingerprint.compose import is_compose_dominant, select_config
    from wendle.fingerprint.dumpsys import foreground_namespace
    from wendle.fingerprint.signature import fingerprint, structural_signature

    print("Connecting...")
    d = u2.connect()
    print(f"  {d.info.get('productName')} {d.info.get('displayWidth')}x{d.info.get('displayHeight')}")
    print(f"\nNAVIGATE for {int(duration)}s: A -> B -> C, then back to A, then B again.\n")

    seen: Dict[str, int] = {}  # fingerprint -> first screen number
    sequence: List[int] = []  # screen numbers in visit order
    last_fp: Optional[str] = None
    n_screens = 0
    n_volatile = 0
    revisit_total = 0

    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            xml1 = d.dump_hierarchy()
            time.sleep(0.3)
            xml2 = d.dump_hierarchy()
            act = _shell(d, "dumpsys activity activities")
            win = _shell(d, "dumpsys window")
        except Exception as e:  # noqa: BLE001
            print("  (capture error, skipping):", e)
            time.sleep(1.0)
            continue

        if not xml1.strip():
            time.sleep(1.0)
            continue

        cfg = select_config(xml1)
        sig1 = structural_signature(xml1, cfg)
        sig2 = structural_signature(xml2, cfg)
        volatile = sig1 != sig2
        ns = foreground_namespace(act, win)
        fp = fingerprint(ns, xml1, cfg)

        # Only treat a SETTLED screen as a node — skip transient mid-scroll /
        # mid-animation frames (the real recorder dumps on settle, §7).
        if volatile:
            n_volatile += 1
            time.sleep(1.2)
            continue

        if fp != last_fp:
            last_fp = fp
            compose = "Y" if is_compose_dominant(xml1) else "N"
            n_nodes = xml1.count("<node")
            if fp in seen:
                tag = f"REVISIT of #{seen[fp]}"
                revisit_total += 1
            else:
                n_screens += 1
                seen[fp] = n_screens
                tag = f"NEW #{n_screens}"
            sequence.append(seen[fp])
            short_ns = ns if len(ns) <= 40 else ns[:37] + "..."
            print(f"  {fp}  compose={compose} nodes={n_nodes:<4} ns={short_ns}  ({tag})")
        time.sleep(1.2)

    print("\n==== Spike 2 fingerprint stability ====")
    print(f"distinct screens .... {n_screens}")
    print(f"revisits detected ... {revisit_total} (each re-used a prior screen's id)")
    print(f"volatile frames skipped {n_volatile} (mid-scroll/animation, not settled)")
    print("visit sequence ...... " + " -> ".join(f"#{n}" for n in sequence))
    print("=======================================")
    print(
        "\nACCEPTANCE (you judge):\n"
        "  • Did returning to an earlier screen print 'REVISIT of #k' with the SAME id\n"
        "    as its first visit?  -> no under-merge.\n"
        "  • Did each genuinely different screen get a different id (a NEW #)?\n"
        "    -> no over-merge.\n"
        "  • Were non-animated screens stable (not flagged volatile)?\n"
        "If a revisit showed a NEW id, or two different screens shared an id, that's a\n"
        "fingerprint bug — capture the two hierarchies and we tune the config."
    )
    return 0


if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace(".", "", 1).isdigit() else 45.0
    sys.exit(main(dur))
