"""The shared DEVICE half of screen entry: settle the live screen under a lock.

Both front-ends use this exact observation discipline before minting through the one
GraphBuilder: the human recorder (RecordSession._observe) and the crawl ingester. One
function so the two cannot drift (same settle loop, same namespace/focus parsing)."""
from __future__ import annotations

import threading
from typing import Optional, Tuple

from wendle.fingerprint.compose import VIEW_PROFILE
from wendle.fingerprint.dumpsys import focused_package, foreground_namespace
from wendle.fingerprint.settle import settle


def observe_settled(driver, lock: threading.Lock, **settle_kwargs
                    ) -> Tuple[str, str, bool, Optional[str]]:
    """Settle the live screen and return (xml, namespace, settled, focus_pkg)."""
    focus_box = {}

    def dump_fn() -> str:
        with lock:
            return driver.dump_hierarchy()

    def ns_fn() -> str:
        with lock:
            act, win = driver.dumps()
        focus_box["f"] = focused_package(win)
        return foreground_namespace(act, win)

    def focus_fn():
        return focus_box.get("f")

    xml, ns, settled = settle(dump_fn, ns_fn, lambda _x: VIEW_PROFILE,
                              focus_fn=focus_fn, **settle_kwargs)
    return xml, ns, settled, focus_box.get("f")
