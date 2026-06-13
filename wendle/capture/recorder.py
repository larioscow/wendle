from __future__ import annotations

from typing import Optional, Tuple

from wendle.calibration.scaling import scale_to_pixels
from wendle.capture.hierarchy import node_at
from wendle.capture.redaction import field_name, is_sensitive
from wendle.capture.selectors import (
    borrow_descendant_selector,
    synthesize_selector,
)
from wendle.capture.types import Gesture, Snapshot
from wendle.models import Action, DeviceProfile, Selector

_ACTION_TYPE = {
    "tap": "click",
    "long_press": "long_click",
    "swipe": "swipe",
}


def _to_pixels(gesture: Gesture, profile: DeviceProfile) -> Tuple[int, int]:
    px = scale_to_pixels(
        gesture.x, abs_min=profile.abs_x[0], abs_max=profile.abs_x[1], screen=profile.display[0]
    )
    py = scale_to_pixels(
        gesture.y, abs_min=profile.abs_y[0], abs_max=profile.abs_y[1], screen=profile.display[1]
    )
    return px, py


def detect_action(
    gesture: Gesture,
    snapshot: Snapshot,
    profile: DeviceProfile,
    *,
    bind_confidence: str = "high",
) -> Tuple[Action, bool]:
    """Turn a segmented gesture + bound snapshot into a semantic Action.

    Returns (action, needs_confirmation). The gesture's raw panel coordinates
    are scaled to pixels, correlated against the snapshot's nodes, and reduced
    to the best stable selector (§8). Sensitive (password) fields are redacted
    at capture (§4) — only a `{param: name}` handle is stored, never a literal.
    `needs_confirmation` is True when the tap-to-hierarchy binding was LOW
    confidence (§5.1) — the edge is provisional, never auto-committed.
    """
    # Multi-finger gestures are not modeled in v1 — flag, never mis-record as a
    # click (§5 step 3). Raising forces the caller to skip it explicitly.
    if gesture.kind == "multi":
        raise ValueError("multi-finger gesture is not recordable in v1; skip it")

    # A binding is uncertain if the §5.1 bind was LOW, the gesture never saw an
    # ABS position, or the contact was flushed truncated (coords are a guess).
    needs_confirmation = (
        bind_confidence == "low" or gesture.position_missing or gesture.truncated
    )
    action_type = _ACTION_TYPE.get(gesture.kind, "click")
    px, py = _to_pixels(gesture, profile)

    # Carry a swipe's end point (scaled) so direction/distance survive (§8).
    end: Optional[Tuple[int, int]] = None
    if gesture.x2 is not None and gesture.y2 is not None:
        ex = scale_to_pixels(
            gesture.x2, abs_min=profile.abs_x[0], abs_max=profile.abs_x[1], screen=profile.display[0]
        )
        ey = scale_to_pixels(
            gesture.y2, abs_min=profile.abs_y[0], abs_max=profile.abs_y[1], screen=profile.display[1]
        )
        end = (ex, ey)

    # A SWIPE is a COORDINATE DRAG, not an element action: faithful replay needs the START point,
    # not a semantic label of whatever element happened to sit under the finger (a scroll starts on
    # an arbitrary list row; a page swipe on the page indicator). Storing a content_desc/text
    # selector here was the bug that left the swipe with an end but no start -> unreplayable. Keep
    # start+end coords; (semantic "swipe element X" / scrollUntilVisible is a separate future path.)
    if action_type == "swipe":
        return (
            Action(
                selector=Selector("coords", (px, py)),
                action_type="swipe",
                replayability="coordinate_only",
                end=end,
            ),
            needs_confirmation,
        )

    node = node_at(snapshot.nodes, px, py)
    if node is None:
        action = Action(
            selector=Selector("coords", (px, py)),
            action_type=action_type,
            replayability="coordinate_only",
            end=end,
        )
        return action, needs_confirmation

    # Decide sensitivity FIRST so the selector synthesis can skip a secret
    # field's visible label (§4 redaction-by-default).
    sensitive = is_sensitive(node)
    selector, replayability = synthesize_selector(node, center=(px, py), sensitive=sensitive,
                                                   frame_nodes=snapshot.nodes)
    # If the clickable target has no own label, borrow one from a labeled child
    # (clickable container + labeled descendant — a very common layout).
    if replayability == "coordinate_only" and not sensitive:
        borrowed = borrow_descendant_selector(node, snapshot.nodes, px, py)
        if borrowed is not None:
            selector, replayability = borrowed
    value = {"param": field_name(node)} if sensitive else None
    action = Action(
        selector=selector,
        action_type=action_type,
        value=value,
        sensitive=sensitive,
        replayability=replayability,
        end=end,
    )
    return action, needs_confirmation
