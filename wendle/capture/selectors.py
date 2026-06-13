from __future__ import annotations

from typing import List, Optional, Tuple

from wendle.capture.types import UINode
from wendle.models import Selector


def _union_count(frame_nodes: List[UINode], value: str) -> int:
    """How many frame nodes the §4 tap-label UNION (text ∪ content-desc) would match —
    mirrors resolution exactly (hints are field handles, never tap targets)."""
    return sum(1 for n in frame_nodes if n.text == value or n.content_desc == value)


def _attr_count(frame_nodes: List[UINode], attr: str, value: str) -> int:
    return sum(1 for n in frame_nodes if getattr(n, attr) == value)


def _text_selector(value: str, frame_nodes: Optional[List[UINode]], replayability: str):
    """The NARROWEST selector that uniquely identifies a text `value` on the capture frame.

    The §4 `label` UNION (text ∪ content-desc ∪ hint) is robust but BROADER than any single
    attribute, so on a frame where a sibling carries the same value in a DIFFERENT attr (a
    Wi-Fi row text='Wi-Fi' beside a Wi-Fi switch content-desc='Wi-Fi') the union matches both
    and replay honestly refuses. So: keep the union when it stays unique (or no frame given —
    today's forward-only behavior); else narrow to exact @text when THAT is unique; else None
    (the caller falls back to a stable handle). One rule, shared by direct + borrowed synthesis."""
    if frame_nodes is None or _union_count(frame_nodes, value) <= 1:
        return Selector("label", value), replayability
    if _attr_count(frame_nodes, "text", value) == 1:
        return Selector("text", value), replayability  # exact @text — hits only this node
    return None


def synthesize_selector(
    node: UINode,
    center: Optional[Tuple[int, int]] = None,
    *,
    sensitive: bool = False,
    field: bool = False,
    frame_nodes: Optional[List[UINode]] = None,
) -> Tuple[Selector, str]:
    """Pick the most stable selector for a node, per the §8 ladder.

    Order: content-desc / text -> resource-id -> coordinates. Returns
    (selector, replayability) where replayability ∈ {high, medium,
    coordinate_only}. `content_desc`/`text` are high; `resource_id` is medium
    (Compose/Flutter often lack it); `coords` is coordinate_only and warns.

    When `sensitive` (a secret field, §4), the `content_desc`/`text` rungs are
    SKIPPED so a secret's visible label can never be baked into the selector;
    such fields resolve to `resource_id` or `coords`.

    When `field` (a text-ENTRY field), the `text` rung is SKIPPED unconditionally:
    a field's `text` is its VOLATILE typed value (and often PII), so binding to it
    would make replay both wait for / match the not-yet-typed value AND leak the
    literal. A field binds to its STABLE handle: resource-id, else its `hint`
    (the placeholder — the stable label of a pure-Compose field, which ships no
    resource-id; §4 of the lazy-region design), else a content-desc (unless
    sensitive — a secret field's visible labels are never baked in), else coords.
    """
    if field:
        if node.resource_id:
            return Selector("resource_id", node.resource_id), "medium"
        if not sensitive and node.hint_text:
            return Selector("hint", node.hint_text), "medium"
        if not sensitive and node.content_desc:
            return Selector("content_desc", node.content_desc), "medium"
        cx, cy = center if center is not None else node.center
        return Selector("coords", (cx, cy)), "coordinate_only"
    if not sensitive:
        # UNIQUENESS-AWARE narrowing (S23 Settings finding): the §4 `label` UNION is robust
        # but BROADER than any single attribute — on a frame where a sibling carries the same
        # value in a different attr (a Wi-Fi row text='Wi-Fi' beside a Wi-Fi switch with
        # content-desc='Wi-Fi'), the union matches both and replay honestly refuses. So prefer
        # the NARROWEST selector that uniquely identifies the tapped node on the capture frame;
        # widen to the union only when it stays unique. (No frame given -> keep the forward-only
        # union, today's behavior — the caller opts in by passing the snapshot.)
        if node.content_desc:
            if frame_nodes is None or _attr_count(frame_nodes, "content_desc", node.content_desc) <= 1:
                return Selector("content_desc", node.content_desc), "high"
            # content-desc collides; fall through to text/resource-id narrowing below
        if node.text:
            # §4 label union when unique on the frame, else exact @text, else fall through to
            # a stable handle (resource-id) — never an ambiguous text selector.
            narrowed = _text_selector(node.text, frame_nodes, "high")
            if narrowed is not None:
                return narrowed
    if node.resource_id:
        return Selector("resource_id", node.resource_id), "medium"
    cx, cy = center if center is not None else node.center
    return Selector("coords", (cx, cy)), "coordinate_only"


def borrow_descendant_selector(
    node: UINode, all_nodes: List[UINode], x: int, y: int
):
    """Synthesize a selector for an unlabeled clickable container from a labeled
    descendant (the §8 "clickable region containing text X" pattern).

    Used when the tapped clickable node has no own text/content-desc/resource-id
    but wraps a labeled child (a very common Android layout). Picks the labeled
    descendant under the tap point (smallest), else the largest labeled
    descendant (the container's primary label). Never borrows from a password
    node. Returns (Selector, "medium") or None.

    Independently-clickable descendants (a nested Switch / Button / Checkbox) are
    AVOIDED: they are their own tap targets — node_at would have returned one if it
    were tapped — so borrowing their label makes the selector resolve to that control
    at replay, firing the WRONG action (the on-device bug where a Wi-Fi *row* tap was
    recorded as the Wi-Fi *toggle* and replay toggled Wi-Fi). Fall back to clickable
    descendants only when the container has no plain (non-clickable) label at all. A
    borrowed label also prefers visible TEXT over content-desc — text is the row label
    a tap reproduces; content-desc more often belongs to a control.
    """
    left, top, right, bottom = node.bounds
    cands: List[UINode] = []
    for n in all_nodes:
        if n is node or n.password:
            continue
        if not (n.content_desc or n.text):
            continue
        nl, nt, nr, nb = n.bounds
        if nl >= left and nt >= top and nr <= right and nb <= bottom:
            cands.append(n)
    if not cands:
        return None
    labels = [n for n in cands if not n.clickable]
    pool = labels or cands  # prefer plain labels; fall back to clickable-only containers
    under = [n for n in pool if n.contains(x, y)]
    best = min(under, key=lambda n: n.area) if under else max(pool, key=lambda n: n.area)
    if best.text:
        # §4: borrowed text anchors are labels too — but narrowed to a UNIQUE selector on the
        # frame (the Wi-Fi-row case: the borrowed row label 'Wi-Fi' must not resolve as a union
        # that also matches the Wi-Fi switch's content-desc; bind exact @text when the union
        # collides). Falls through to content-desc only if even exact text is ambiguous.
        narrowed = _text_selector(best.text, all_nodes, "medium")
        if narrowed is not None:
            return narrowed
    if best.content_desc and _attr_count(all_nodes, "content_desc", best.content_desc) <= 1:
        return Selector("content_desc", best.content_desc), "medium"
    return None  # no unique label could be borrowed — caller keeps the coordinate fallback
