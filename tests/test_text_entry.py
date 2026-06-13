from wendle.capture.text_entry import detect_text_entry
from wendle.capture.types import UINode


def _field(rid, text, *, password=False, focused=True, desc=""):
    return UINode(
        cls="android.widget.EditText",
        resource_id=rid,
        text=text,
        content_desc=desc,
        clickable=True,
        password=password,
        bounds=(0, 0, 100, 50),
        focused=focused,
    )


def test_regular_field_records_literal_text():
    before = [_field("com.app:id/user", "")]
    after = [_field("com.app:id/user", "alice")]
    action = detect_text_entry(before, after)
    assert action.action_type == "set_text"
    assert action.sensitive is False
    assert action.value == {"text": "alice"}


def test_password_field_is_redacted_to_param_handle():
    before = [_field("com.app:id/password", "", password=True)]
    after = [_field("com.app:id/password", "hunter2", password=True)]
    action = detect_text_entry(before, after)
    assert action.action_type == "set_text"
    assert action.sensitive is True
    assert action.value == {"param": "password"}
    # the secret literal never appears anywhere on the action
    assert "hunter2" not in repr(action)


def test_no_change_returns_none():
    same = [_field("com.app:id/user", "alice")]
    assert detect_text_entry(same, [_field("com.app:id/user", "alice")]) is None


def test_no_focused_editable_returns_none():
    assert detect_text_entry([], []) is None


def _bank_field(text):
    # an app's OWN field whose class path merely CONTAINS an IME package marker
    # ('.imexpress' contains '.ime') — review finding 7: package markers applied to the
    # CLASS misclassified it as keyboard and its typing was silently never captured.
    return UINode(
        cls="com.bank.imexpress.CustomEditText", resource_id="com.bank:id/user",
        text=text, content_desc="", clickable=True, password=False,
        bounds=(0, 0, 1080, 120), focused=True, package="com.bank",
    )


def test_app_field_with_ime_like_class_is_still_captured():
    action = detect_text_entry([_bank_field("")], [_bank_field("alice")])
    assert action is not None and action.value == {"text": "alice"}


def test_ime_extract_mirror_excluded_for_any_vendor_keyboard():
    # the IME's own editable mirror is excluded by its FRAMEWORK class prefix
    # (android.inputmethodservice.*), independent of the keyboard vendor's package —
    # honeyboard has no IME_PKG_SEGMENTS component and must still be excluded.
    def mirror(text):
        return UINode(
            cls="android.inputmethodservice.ExtractEditText", resource_id="",
            text=text, content_desc="", clickable=True, password=False,
            bounds=(0, 1500, 1080, 1600), focused=True,
            package="com.samsung.android.honeyboard",
        )
    assert detect_text_entry([mirror("")], [mirror("alice")]) is None


def test_keyboard_field_by_resource_id_namespace_excluded():
    # a keyboard-owned editable identified only by its resource-id NAMESPACE (package attr
    # missing from the dump) is still the keyboard's, never the app's target.
    def search_strip(text):
        return UINode(
            cls="android.widget.EditText",
            resource_id="com.google.android.inputmethod.latin:id/search_strip",
            text=text, content_desc="", clickable=True, password=False,
            bounds=(0, 1500, 1080, 1600), focused=True,
        )
    assert detect_text_entry([search_strip("")], [search_strip("gif")]) is None
