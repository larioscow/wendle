from __future__ import annotations

from wendle.capture.types import UINode


def is_sensitive(node: UINode) -> bool:
    """True when a node is a secret field whose typed value must never be stored.

    v1 signal available from the hierarchy dump: the `password` attribute
    (UIAutomator surfaces AccessibilityNodeInfo.isPassword()). FLAG_SECURE /
    inputType variations are added when the AccessibilityService enrichment
    (§5.x) lands; until then `password` is the floor signal.
    """
    return node.password


def field_name(node: UINode) -> str:
    """A stable parameter name for a redacted field, derived from its id/desc.

    The resource-id leaf is preferred. `content_desc` is used only for
    NON-sensitive fields — a secret field's visible label must not leak even
    into the parameter name — so a sensitive field with no resource-id becomes
    the generic "field".
    """
    if node.resource_id and "/" in node.resource_id:
        return node.resource_id.rpartition("/")[2]
    if node.content_desc and not is_sensitive(node):
        return node.content_desc.strip().lower().replace(" ", "_")
    return "field"
