"""Compose text-entry capture: the recorder must capture typing into a Jetpack Compose field
(Gemini), not just a classic focused EditText. Compose assigns class=android.widget.EditText to
editable fields but lands `focused` on the AndroidComposeView host — so the old `focused AND
EditText` gate captured NOTHING. Detection is now decoupled (is_editable + a focus-tolerant
pick_ime_target); recovery is the unchanged text-diff (node.text carries the live buffer).
"""
from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.text_entry import (
    detect_text_entry,
    field_identity,
    is_editable,
    pick_ime_target,
)
from wendle.capture.types import UINode


def _node(cls, *, rid="", text="", desc="", hint="", focused="false", password="false",
          bounds="[0,100][1080,200]", pkg="com.app"):
    return (f'<node class="{cls}" package="{pkg}" resource-id="{rid}" text="{text}" '
            f'content-desc="{desc}" hint="{hint}" focused="{focused}" password="{password}" '
            f'clickable="false" checkable="false" checked="false" selected="false" bounds="{bounds}"/>')


def _nodes(*nodes):
    return parse_hierarchy("<hierarchy>" + "".join(nodes) + "</hierarchy>")


def _host(focused="true"):
    return _node("androidx.compose.ui.platform.AndroidComposeView", focused=focused, bounds="[0,0][1080,2400]")


IME = _node("android.inputmethodservice.SoftInputWindow",
            pkg="com.google.android.inputmethod.latin", bounds="[0,1400][1080,2400]")


def test_compose_text_entry_captured_despite_focus_on_host():
    # Gemini regression: editable field reports class=EditText, focused=false (focus on the host),
    # IME up, text grows h -> hello. Must now capture a set_text.
    def field(t):
        return _node("android.widget.EditText", desc="Ask Gemini", text=t, focused="false")
    a = detect_text_entry(_nodes(_host(), field("h"), IME), _nodes(_host(), field("hello"), IME))
    assert a is not None and a.action_type == "set_text" and a.value == {"text": "hello"}
    assert a.selector.kind != "text"  # bound to a stable handle, never the typed value


def test_compose_host_focus_is_not_chosen_as_target():
    nodes = _nodes(_host(focused="true"),
                   _node("android.widget.EditText", desc="Ask", text="x", focused="false"), IME)
    t = pick_ime_target(nodes)
    assert t is not None and t.cls.endswith("EditText")  # picked the field, not the focused host


def test_is_editable_ladder():
    def n(cls, password=False):
        return UINode(cls=cls, resource_id="", text="", content_desc="", clickable=False,
                      password=password, bounds=(0, 0, 1, 1))
    assert is_editable(n("android.widget.EditText"))                                    # A1
    assert is_editable(n("androidx.appcompat.widget.AppCompatEditText"))                # A1
    assert is_editable(n("com.google.android.material.textfield.TextInputEditText"))    # A1
    assert is_editable(n("android.widget.AutoCompleteTextView"))                        # A2
    assert is_editable(n("android.widget.SearchView$SearchAutoComplete"))               # A2
    assert is_editable(n("android.view.View", password=True))                           # A3
    assert not is_editable(n("android.widget.Button"))
    assert not is_editable(n("android.widget.TextView"))


def test_compose_password_redacted_no_literal_stored():
    # secured Compose field (password=true); masked text grows. Capture a param handle ONLY,
    # never the value, and never leak the label into the param name.
    def field(t):
        return _node("android.widget.EditText", desc="Password", text=t, focused="false", password="true")
    a = detect_text_entry(_nodes(_host(), field(""), IME), _nodes(_host(), field("•••"), IME))
    assert a is not None and a.sensitive is True
    assert "param" in a.value and "text" not in a.value      # literal never stored
    assert a.value["param"] == "field"                       # no rid + sensitive label -> generic
    assert a.selector.kind != "text"


def test_multi_field_compose_login_keeps_distinct_identities():
    # two Compose fields, both resource_id='' — identity must fall back (content_desc/position),
    # never collapse to one (a multi-field login).
    email = _node("android.widget.EditText", desc="Email", bounds="[0,100][1080,200]")
    pw = _node("android.widget.EditText", desc="Password", bounds="[0,300][1080,400]")
    e, p = parse_hierarchy("<hierarchy>" + email + "</hierarchy>")[0], parse_hierarchy("<hierarchy>" + pw + "</hierarchy>")[0]
    assert field_identity(e) != field_identity(p)


def test_ambiguous_unfocused_editables_returns_none():
    # two editable fields, none focused, IME up -> can't tell which -> honest None (probe).
    f1 = _node("android.widget.EditText", desc="Email", text="x", bounds="[0,100][1080,200]")
    f2 = _node("android.widget.EditText", desc="Password", text="y", bounds="[0,300][1080,400]")
    assert pick_ime_target(_nodes(f1, f2, IME)) is None


def test_ime_extract_edittext_is_not_the_target():
    # In fullscreen/extract IME mode the keyboard exposes its OWN ExtractEditText (class ends in
    # 'EditText', carries the composing buffer). It must NEVER be picked over the app's field —
    # else we drop the real capture or bind to the keyboard's mirror (confident-wrong, invariant #1).
    extract = _node("com.android.inputmethodservice.ExtractEditText",
                    pkg="com.google.android.inputmethod.latin", text="hello", focused="true",
                    bounds="[0,1400][1080,1500]")
    app_field = _node("android.widget.EditText", desc="Ask Gemini", text="hello", focused="false")
    t = pick_ime_target(_nodes(_host(), app_field, extract, IME))
    assert t is not None and t.package == "com.app"  # the APP field, not the IME mirror


def test_classic_edittext_still_captured():
    def field(t):
        return _node("android.widget.EditText", rid="com.app:id/user", text=t, focused="true")
    a = detect_text_entry(_nodes(field("a"), IME), _nodes(field("alice"), IME))
    assert a is not None and a.value == {"text": "alice"}


def test_no_text_change_returns_none():
    def field(t):
        return _node("android.widget.EditText", desc="Ask", text=t, focused="false")
    assert detect_text_entry(_nodes(_host(), field("hi"), IME), _nodes(_host(), field("hi"), IME)) is None


def test_placeholder_hint_never_captured_as_value():
    # #17: an EMPTY field renders its HINT in node.text (text == hint, e.g. 'Message MiniMax').
    # That is NOT typed input — capturing it would type the placeholder on replay. Honesty-first:
    # text == hint is treated as empty, so a focus-then-blur on an empty field yields no set_text.
    def field(t, hint):
        return _node("android.widget.EditText", desc="", text=t, hint=hint, focused="true")
    a = detect_text_entry(_nodes(field("", "Message MiniMax"), IME),
                          _nodes(field("Message MiniMax", "Message MiniMax"), IME))
    assert a is None


def test_real_text_over_hint_captured_without_the_hint():
    # typing real text into a hinted field captures ONLY the real value, never the hint.
    def field(t, hint):
        return _node("android.widget.EditText", desc="", text=t, hint=hint, focused="true")
    a = detect_text_entry(_nodes(field("", "Message MiniMax"), IME),
                          _nodes(field("hi", "Message MiniMax"), IME))
    assert a is not None and a.value == {"text": "hi"}
