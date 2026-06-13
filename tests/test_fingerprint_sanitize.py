from wendle.fingerprint.signature import (
    MALFORMED,
    FingerprintConfig,
    fingerprint,
    structural_signature,
)

APP = (
    '<node class="android.widget.FrameLayout" package="com.app" resource-id="com.app:id/root" '
    'clickable="false" content-desc="" text="" bounds="[0,0][1080,2000]">'
    '<node class="android.widget.Button" package="com.app" resource-id="com.app:id/ok" '
    'clickable="true" content-desc="" text="OK" bounds="[0,0][200,100]"/></node>'
)
IME = (
    '<node class="android.inputmethodservice.SoftInputWindow" package="com.google.android.inputmethod.latin" '
    'resource-id="" clickable="false" content-desc="" text="" bounds="[0,2000][1080,2400]">'
    '<node class="android.widget.FrameLayout" package="com.google.android.inputmethod.latin" '
    'resource-id="com.google.android.inputmethod.latin:id/keyboard_holder" clickable="false" '
    'content-desc="" text="" bounds="[0,2000][1080,2400]"/></node>'
)


def _h(*nodes):
    return "<hierarchy>" + "".join(nodes) + "</hierarchy>"


def test_ime_stripped_keyboard_up_down_same_fp():
    up = structural_signature(_h(APP, IME), focus_pkg="com.app")
    down = structural_signature(_h(APP), focus_pkg="com.app")
    assert up == down  # keyboard presence must not fork the screen


def test_ime_not_stripped_when_ime_is_focused():
    # an IME settings / keyboard app under test -> its content must survive
    with_focus = structural_signature(_h(APP, IME), focus_pkg="com.google.android.inputmethod.latin")
    assert "keyboard_holder" in with_focus


def test_ime_stripped_even_without_focus_known():
    # focus_pkg=None still strips the IME (it's never the screen-of-interest)
    assert structural_signature(_h(APP, IME)) == structural_signature(_h(APP))


SYSUI = (
    '<node class="android.widget.FrameLayout" package="com.android.systemui" '
    'resource-id="com.android.systemui:id/status_bar" clickable="false" content-desc="" text="" '
    'bounds="[0,0][1080,80]">'
    '<node class="android.widget.TextView" package="com.android.systemui" '
    'resource-id="com.android.systemui:id/ongoing_activity_chip" clickable="false" content-desc="" '
    'text="" bounds="[0,0][200,80]"/></node>'
)


def test_systemui_overlay_stripped_when_app_focused():
    # status-bar capsule churn must not fork an app screen
    with_bar = structural_signature(_h(SYSUI, APP), focus_pkg="com.app")
    without = structural_signature(_h(APP), focus_pkg="com.app")
    assert with_bar == without


def test_systemui_kept_when_it_is_the_screen():
    # the notification shade IS the screen -> not stripped
    sig = structural_signature(_h(SYSUI, APP), focus_pkg="com.android.systemui")
    assert "ongoing_activity_chip" in sig or "status_bar" not in sig  # not pruned as overlay


def test_app_clock_container_survives_systemui_denylist():
    app_clock = _h(
        '<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/clock" '
        'clickable="true" content-desc="" text="" bounds="[0,0][100,50]"/>'
    )
    assert "com.app:id/clock" in structural_signature(app_clock)


def test_malformed_xml_returns_sentinel_no_crash():
    assert structural_signature("") == MALFORMED
    assert structural_signature("   ") == MALFORMED
    assert structural_signature("<hierarchy><node unclosed") == MALFORMED
    # fingerprint must not raise either
    assert isinstance(fingerprint("ns", ""), str)
