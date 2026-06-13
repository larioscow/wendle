from __future__ import annotations

import re
from typing import List, Optional

# defusedxml hardens against XXE / billion-laughs; the hierarchy XML comes from
# the device's UIAutomator dump (semi-trusted), so we never use the stdlib parser.
from defusedxml.ElementTree import fromstring as _xml_fromstring

from wendle.capture.types import UINode
from wendle.fingerprint.signature import SYSTEMUI_PKG

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _bool(attr: str) -> bool:
    return attr == "true"


def parse_hierarchy(xml: str, focus_pkg: Optional[str] = None) -> List[UINode]:
    """Parse a UIAutomator hierarchy dump into a flat list of UINode.

    Uses the stdlib XML parser (no lxml dependency). Nodes without a valid
    `bounds` attribute are skipped.

    RULE 1 (general, app-agnostic): when the foreground app is known (`focus_pkg`),
    drop `com.android.systemui` nodes (the status/nav-bar chrome — the OS clock,
    battery, etc.) so a tap can NEVER bind to system UI. This is the same focus-gated
    test the fingerprint uses (`signature._should_prune`), applied to the tap-binding
    node list. IME nodes are kept on purpose — the recorder needs them to find the
    keyboard region for keystroke suppression. With `focus_pkg=None` nothing is
    stripped (keeps XML-only unit tests, and any deliberate status-bar recording).
    """
    root = _xml_fromstring(xml)
    nodes: List[UINode] = []
    strip_systemui = focus_pkg is not None and focus_pkg != SYSTEMUI_PKG
    for el in root.iter("node"):
        m = _BOUNDS.search(el.get("bounds", ""))
        if not m:
            continue
        pkg = el.get("package", "")
        if strip_systemui and pkg == SYSTEMUI_PKG:
            continue  # status/nav-bar chrome is not the app — never a tap target
        left, top, right, bottom = map(int, m.groups())
        nodes.append(
            UINode(
                cls=el.get("class", ""),
                resource_id=el.get("resource-id", ""),
                text=el.get("text", ""),
                content_desc=el.get("content-desc", ""),
                clickable=_bool(el.get("clickable", "false")),
                password=_bool(el.get("password", "false")),
                bounds=(left, top, right, bottom),
                focused=_bool(el.get("focused", "false")),
                checkable=_bool(el.get("checkable", "false")),
                checked=_bool(el.get("checked", "false")),
                selected=_bool(el.get("selected", "false")),
                package=pkg,
                hint_text=el.get("hint", ""),  # placeholder hint (uiautomator exposes it as `hint`)
            )
        )
    return nodes


def node_at(nodes: List[UINode], x: int, y: int) -> Optional[UINode]:
    """Return the most specific node at (x, y).

    Prefers the smallest-area *clickable* node containing the point; falls back
    to the smallest containing node when none is clickable.
    """
    containing = [n for n in nodes if n.contains(x, y)]
    if not containing:
        return None
    clickable = [n for n in containing if n.clickable]
    pool = clickable or containing
    # Deterministic tie-break for equal-area overlaps (else iteration-order dependent).
    return min(pool, key=lambda n: (n.area, n.cls, n.resource_id))


def plausible_bind_target(nodes: List[UINode], x: int, y: int) -> bool:
    """Was the node that WILL be bound at (x, y) something a user could plausibly have aimed
    at? Plausible = clickable OR labeled (text/content-desc — Compose routinely dumps tappable
    semantics with clickable=false). Judged on the EXACT node `node_at` selects, not on "any
    node containing the point": a labeled card with a smaller unlabeled shimmer rect on top
    binds the shimmer, so the card's label must not bless it. A frame whose bind target is an
    unlabeled non-clickable node (a lagging mid-load overlay, a shimmer placeholder, an empty
    shell) did not show what the user acted on — binding a selector there records a
    plausible-but-wrong element. One rule, app-agnostic; no widget-class lists."""
    n = node_at(nodes, x, y)
    return n is not None and bool(n.clickable or n.text or n.content_desc)
