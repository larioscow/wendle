from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

from wendle.fingerprint.signature import FingerprintConfig, structural_signature


def settle(
    dump_fn: Callable[[], str],
    ns_fn: Callable[[], str],
    config_fn: Callable[[str], FingerprintConfig],
    *,
    focus_fn: Optional[Callable[[], Optional[str]]] = None,
    need: int = 3,
    interval: float = 0.4,
    max_wait: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Tuple[str, str, bool]:
    """Wait for a screen to SETTLE before fingerprinting it.

    The on-device capture/recorder MUST NOT fingerprint mid-transition (launcher
    while an app loads, async content streaming in, a keyboard animating). A weak
    two-dump check caught brief stable instants mid-transition and produced
    garbage (the corpus showed launcher namespaces labeled as other apps).

    Settled = `need` consecutive dumps with the SAME structural signature AND the
    SAME namespace. Returns (last_xml, last_namespace, settled). `settled=False`
    means it never stabilized within `max_wait` — a genuinely volatile/live screen
    (feed, chat stream) that the caller should treat as namespace-dominant (§7),
    not commit as a stable structural node.
    """
    deadline = clock() + max_wait
    prev_sig: Optional[str] = None
    prev_ns: Optional[str] = None
    consecutive = 1
    xml = ns = ""
    while True:
        xml = dump_fn()
        ns = ns_fn()
        focus = focus_fn() if focus_fn else None
        # Sanitize during the stillness check too — else IME/status-bar churn
        # (a foreign overlay) prevents a real screen from ever settling.
        sig = structural_signature(xml, config_fn(xml), focus_pkg=focus)
        if prev_sig is not None and sig == prev_sig and ns == prev_ns:
            consecutive += 1
        else:
            consecutive = 1
        prev_sig, prev_ns = sig, ns
        if consecutive >= need:
            return xml, ns, True
        if clock() >= deadline:
            return xml, ns, False
        sleep(interval)
