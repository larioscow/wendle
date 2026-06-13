"""B1 — a text-entry field must bind to a STABLE handle, never to its typed value.

The recorder previously stored the typed text as the field's selector (text rung beats the
resource-id rung), which made replay (a) wait for / match a value that isn't on screen yet and
(b) leak the literal (PII) into the graph and logs.
"""
from wendle.capture.selectors import synthesize_selector
from wendle.capture.text_entry import detect_text_entry
from wendle.capture.types import UINode
from wendle.models import Selector


def _field(rid, text, cd="", password=False):
    return UINode(cls="android.widget.EditText", resource_id=rid, text=text, content_desc=cd,
                  clickable=True, password=password, bounds=(0, 0, 100, 100), focused=True)


def test_field_selector_prefers_resource_id_not_typed_text():
    sel, rep = synthesize_selector(_field("com.app:id/user", "33 1129 960"), field=True)
    assert sel == Selector("resource_id", "com.app:id/user") and rep == "medium"
    assert "1129" not in str(sel.value)  # the typed value never reaches the selector


def test_field_selector_falls_back_to_content_desc_then_coords():
    assert synthesize_selector(_field("", "alice", cd="Username"), field=True)[0] == \
        Selector("content_desc", "Username")
    sel, rep = synthesize_selector(_field("", "alice"), field=True)
    assert sel.kind == "coords" and rep == "coordinate_only"  # nothing stable -> honest coords


def test_sensitive_field_never_uses_label():
    # a secret field skips content-desc too -> resource-id (or coords), never the visible label
    sel, _ = synthesize_selector(_field("com.app:id/pw", "hunter2", cd="Password"),
                                 sensitive=True, field=True)
    assert sel == Selector("resource_id", "com.app:id/pw")


def test_detect_text_entry_binds_field_to_stable_id_not_value():
    before = [_field("com.app:id/card", "")]
    after = [_field("com.app:id/card", "5264")]
    action = detect_text_entry(before, after)
    assert action.action_type == "set_text"
    assert action.selector == Selector("resource_id", "com.app:id/card")
    assert "5264" not in str(action.selector.value)  # value not baked into the selector


def test_tap_target_selector_unchanged():
    # a plain tap target (button/label) still uses content-desc/text first (field=False default)
    btn = UINode(cls="android.widget.Button", resource_id="com.app:id/go", text="Continuar",
                 content_desc="", clickable=True, password=False, bounds=(0, 0, 100, 100))
    assert synthesize_selector(btn)[0] == Selector("label", "Continuar")
