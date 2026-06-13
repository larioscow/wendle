"""Device-confirmed (Galaxy S23, Android 16) regression: a password EditText's uiautomator dump
LEAKS the typed literal in its `text` attribute — so redaction is load-bearing. The recorder must
strip it end-to-end (selector binds the stable handle, value is a {param}, never the literal), and
the masked verify_text rule must pass on the non-empty field WITHOUT comparing the secret.
"""
from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.redaction import is_sensitive
from wendle.capture.text_entry import detect_text_entry

SECRET = "SuperSecret$42"  # never appears in any recorded artifact


def _wifi_password_dump(field_text):
    # the exact shape captured on-device: a password EditText whose `text` carries the literal.
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="com.android.settings" '
            f'resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.EditText" package="com.android.settings" '
            f'resource-id="com.android.settings:id/edittext" password="true" focused="true" '
            f'clickable="true" content-desc="" text="{field_text}" bounds="[40,400][1040,520]"/>'
            f'</node></hierarchy>')


def test_real_password_dump_leaks_literal_but_recorder_redacts_it():
    leaking = _wifi_password_dump(SECRET)
    assert SECRET in leaking  # the device dump DOES leak it — redaction is the only guard

    nodes = parse_hierarchy(leaking, focus_pkg="com.android.settings")
    pw = next(n for n in nodes if getattr(n, "password", False))
    assert is_sensitive(pw)  # detected via the password attribute

    before = parse_hierarchy(_wifi_password_dump(""), focus_pkg="com.android.settings")
    action = detect_text_entry(before, nodes)
    assert action is not None and action.sensitive
    # the recorded action binds the STABLE handle and a {param}, NEVER the literal
    assert action.selector.kind == "resource_id"
    assert action.value == {"param": "edittext"}
    assert SECRET not in repr(action.selector.value) and SECRET not in str(action.value)


def test_masked_verify_passes_on_nonempty_without_comparing_the_secret():
    # the device finding: a password field's text is NON-EMPTY (the literal is present), so the
    # masked rule (len>0, never == secret) confirms landing without a false-stop and without a leak.
    from wendle.driver.fake import FakeDriver
    from wendle.models import Selector
    drv = FakeDriver()
    sel = Selector("resource_id", "com.android.settings:id/edittext")
    drv.focus_and_type(sel, SECRET)  # the field now holds the value (non-empty)
    assert drv.verify_text(sel, expected="WRONG_GUESS", masked=True) is True  # len>0, not compared
    drv2 = FakeDriver()
    assert drv2.verify_text(sel, expected=SECRET, masked=True) is False  # empty field -> honest no
