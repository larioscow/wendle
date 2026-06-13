from __future__ import annotations

from typing import List, Optional

from wendle.capture.redaction import field_name, is_sensitive
from wendle.capture.selectors import synthesize_selector
from wendle.capture.types import UINode
from wendle.fingerprint.signature import is_ime_class, is_ime_pkg
from wendle.models import Action, Selector

# Classic-View editable classes that do NOT contain 'EditText' (the substring check misses them).
_EDITABLE_NON_EDITTEXT = ("AutoCompleteTextView", "MultiAutoCompleteTextView",
                          "SearchView$SearchAutoComplete")


def is_editable(node: UINode) -> bool:
    """True when a node is a TEXT-INPUT field — toolkit-agnostic, keyed on what the raw UIAutomator
    dump actually carries (it exposes no `editable` attribute). An ordered capability ladder:
      A1  class ends in 'EditText' — View EditText/AppCompatEditText/TextInputEditText AND a
          standalone Jetpack Compose TextField/BasicTextField AND a visible WebView <input> (Compose's
          a11y delegate assigns class=android.widget.EditText to anything with EditableText semantics).
      A2  an explicit known-editable class that lacks 'EditText' (AutoCompleteTextView, …).
      A3  a password field — editable by definition even when its class is generic/odd.
    (A4 — the platform `isEditable`/ACTION_SET_TEXT signal that catches a Compose buffer merged onto a
    cls=View ancestor — needs the enriched-dump path and is deferred.)"""
    cls = node.cls
    return (cls.endswith("EditText")
            or any(k in cls for k in _EDITABLE_NON_EDITTEXT)
            or node.password)


def is_ime_node(node: UINode) -> bool:
    """ONE rule for 'this node belongs to the soft keyboard' (capture + suppression share it):
    the node's PACKAGE is an IME package, its resource-id lives in an IME package's NAMESPACE
    (covers dumps without a package attr), or its class is an input-method FRAMEWORK widget —
    ExtractEditText, the IME's own editable mirror, ends in 'EditText' and must NEVER be
    mistaken for the app's field. Package markers are never matched against class paths: an
    app's '.imexpress' class is not a keyboard (review finding 7 — the false positive silently
    dropped the app's text capture). Residual: a vendor keyboard node with no package attr, no
    resource-id AND a non-framework class is missed here — the recorder's REGION suppression
    (the window root is framework-classed) is the belt for that case."""
    return (is_ime_pkg(node.package) or is_ime_class(node.cls)
            or (bool(node.resource_id) and is_ime_pkg(node.resource_id.split(":")[0])))


_is_ime = is_ime_node  # internal alias (the module's own call sites)


def _ime_up(nodes: List[UINode]) -> bool:
    """True when a soft-keyboard (IME) window is present — text entry is plausible this cycle
    (necessary, not sufficient: always conjoined with an editable target)."""
    return any(_is_ime(n) for n in nodes)


def pick_ime_target(nodes: List[UINode]) -> Optional[UINode]:
    """The editable field being typed into, DECOUPLED from the brittle `focused` flag. Compose
    routinely lands focus on the AndroidComposeView host (or no node), so the old
    `focused AND EditText` gate silently missed every Compose field — the Gemini zero-capture.

    Ladder: a single focused editable wins (classic View); else, if the IME is up and exactly one
    editable field exists on screen, it IS the target even when `focused` is false (Compose); an
    ambiguous case (multiple candidates, none clearly the target) is NOT guessed — return None and
    let the caller stay an honest probe rather than diff the wrong field (invariant #1).

    The IME's own editable mirror (ExtractEditText) is EXCLUDED from the candidates — it belongs to
    the keyboard, not the app (the app-vs-system boundary RULE 1 enforces for system UI). A
    programmatic change to the sole editable while the IME is up (autofill / a live counter) is the
    accepted B2 residual — rare, and a placeholder/autofill value is plausibly worth replaying."""
    editable = [n for n in nodes if is_editable(n) and not _is_ime(n)]
    if not editable:
        return None
    focused = [n for n in editable if n.focused]
    if len(focused) == 1:
        return focused[0]
    if len(editable) == 1 and _ime_up(nodes):  # the sole editable; focused==0 here (len 1 returned above)
        return editable[0]
    return None


def field_identity(node: UINode) -> str:
    """A stable per-field key for matching the same field across cycles and detecting a field
    SWITCH. resource_id is EMPTY for Compose/WebView, so fall back to content_desc, then a coarse
    positional key — never collapse two Compose fields (a multi-field login) to one identity."""
    if node.resource_id:
        return "rid:" + node.resource_id
    if node.content_desc:
        return "desc:" + node.content_desc
    left, top, _r, _b = node.bounds
    return f"pos:{left},{top}"


def _effective_text(node: UINode) -> str:
    """The field's TYPED content. An empty field renders its placeholder HINT in node.text (e.g.
    'Message MiniMax', 'Search settings'); that hint is NOT user input, so text == hint reads as
    empty. Prevents capturing a placeholder as a value (which would type the hint on replay). (#17)"""
    return "" if (node.hint_text and node.text == node.hint_text) else node.text


def detect_text_entry(
    before: List[UINode], after: List[UINode]
) -> Optional[Action]:
    """Capture a text-entry as a `set_text` Action by diffing the focused field.

    Soft keyboards emit no usable key events (§5 step 6), so text entry is recovered by comparing
    the editable field's text before vs after — which works for Compose too, because node.text is
    populated from the live EditableText buffer (the Gemini miss was DETECTION, not value-read).
    The target is chosen FOCUS-TOLERANTLY (pick_ime_target). Returns None when nothing changed.
    Secret fields are redacted at capture (§4): the literal is never stored — only a `{param: name}`
    handle.
    """
    node_after = pick_ime_target(after)
    if node_after is None:
        return None
    # Match the SAME field across the diff (resource_id is empty for Compose, so identity falls
    # back to content_desc / position — see field_identity).
    node_before = pick_ime_target(before)
    if node_before is not None and field_identity(node_before) != field_identity(node_after):
        node_before = None
    old_text = _effective_text(node_before) if node_before is not None else ""
    new_text = _effective_text(node_after)  # placeholder-aware: a hint reads as empty
    if new_text == old_text:
        return None  # no change (incl. focus-then-blur on an empty, hint-showing field)

    sensitive = is_sensitive(node_after)
    # field=True: bind to the field's STABLE handle (resource-id / hint), NEVER its typed text
    # (which is the volatile value and may be PII) — fixes replay waiting on the not-yet-typed
    # value and the value-as-selector leak.
    selector, replayability = synthesize_selector(node_after, sensitive=sensitive, field=True)
    if sensitive:
        value = {"param": field_name(node_after)}  # literal discarded
    else:
        value = {"text": new_text}
    return Action(
        selector=selector,
        action_type="set_text",
        value=value,
        sensitive=sensitive,
        replayability=replayability,
    )


def detect_checkable_entry(src_node: Optional[UINode], after: List[UINode]) -> Optional[Action]:
    """A checkbox/switch/radio tap that flipped widget STATE but NOT the screen fingerprint.

    Grounded in Playwright `setChecked(bool)` / DroidBot `SelectEvent`: record the DESIRED
    boolean (the SETTLED state), never a blind tap — so replay is idempotent. The flip is
    only visible AFTER the tap, so re-find the SAME widget by resource-id in the `after`
    snapshot (do NOT node_at the after-snapshot — the box may have moved/reflowed). Returns
    None unless a real, confidently-readable flip occurred; otherwise the caller keeps it an
    honest `probe` rather than a phantom action.
    """
    if src_node is None or not src_node.checkable or not src_node.resource_id:
        return None  # only real, identifiable checkables promote (else stay a probe)
    after_node = next(
        (n for n in after if n.resource_id == src_node.resource_id and n.checkable), None
    )
    if after_node is None:
        return None  # Compose semantics-only toggle (no readable state) -> stay a probe
    # The flip lives in `checked` (checkbox/switch/radio) OR `selected` (segmented/tab).
    if after_node.checked != src_node.checked:
        target = after_node.checked
    elif after_node.selected != src_node.selected:
        target = after_node.selected
    else:
        return None  # genuine no-op -> stay a probe
    # Bind to the stable resource_id, NEVER the label: a stateful widget's text/content-desc
    # IS its state ('Wi-Fi, On' / 'ON'), so a label selector would only match the recorded
    # state and fail the opposite-state mismatch case set_checked exists to fix.
    return Action(
        selector=Selector("resource_id", after_node.resource_id),
        action_type="set_checked",
        value={"checked": target},  # the desired, settled boolean
        sensitive=is_sensitive(after_node),
        replayability="medium",
        intent="navigate",
    )
