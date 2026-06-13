from wendle.fingerprint.dumpsys import (
    foreground_namespace,
    parse_focused_window,
    parse_resumed_activity,
)

ACTIVITY_DUMP = """
  Stack #0:
    mResumedActivity: ActivityRecord{a1b2c3 u0 com.whatsapp/.HomeActivity t42}
    Running activities (most recent first):
"""

WINDOW_ACTIVITY = "  mCurrentFocus=Window{1a2b u0 com.whatsapp/.HomeActivity}\n  mFocusedApp=..."
WINDOW_SHADE = "  mCurrentFocus=Window{9z8y u0 NotificationShade}\n"


def test_parse_resumed_activity():
    assert parse_resumed_activity(ACTIVITY_DUMP) == "com.whatsapp/.HomeActivity"


def test_parse_resumed_activity_none_when_absent():
    assert parse_resumed_activity("no activity here") is None


def test_parse_focused_window_activity():
    assert parse_focused_window(WINDOW_ACTIVITY) == "com.whatsapp/.HomeActivity"


def test_parse_focused_window_system_surface():
    assert parse_focused_window(WINDOW_SHADE) == "NotificationShade"


def test_namespace_prefers_resumed_activity():
    ns = foreground_namespace(ACTIVITY_DUMP, WINDOW_ACTIVITY)
    assert ns == "com.whatsapp/.HomeActivity"


def test_namespace_uses_system_window_when_not_an_activity():
    # shade is focused; its own namespace even though an activity is resumed underneath
    ns = foreground_namespace(ACTIVITY_DUMP, WINDOW_SHADE)
    assert ns == "NotificationShade"


def test_namespace_unknown_when_nothing_parses():
    assert foreground_namespace("", "") == "unknown"


def test_parse_top_resumed_activity_android12plus():
    dump = "  topResumedActivity: ActivityRecord{ff11 u0 com.banregio.hey/.MainActivity t9}"
    assert parse_resumed_activity(dump) == "com.banregio.hey/.MainActivity"


def test_overlay_from_other_package_uses_focused_window():
    act = "topResumedActivity: ActivityRecord{a u0 com.app/.Main t1}"
    win = "mCurrentFocus=Window{b u0 com.android.permissioncontroller/.GrantActivity}"
    assert foreground_namespace(act, win) == "com.android.permissioncontroller/.GrantActivity"


def test_recents_over_launcher_uses_focused_window():
    act = "topResumedActivity: ActivityRecord{a u0 com.sec.android.app.launcher/.Home t1}"
    win = "mCurrentFocus=Window{b u0 com.android.systemui/.recents.RecentsActivity}"
    assert foreground_namespace(act, win) == "com.android.systemui/.recents.RecentsActivity"


def test_token_less_window_still_parses():
    assert parse_focused_window("mCurrentFocus=Window{abcd NotificationShade}") == "NotificationShade"


def test_focused_package_from_activity_window():
    from wendle.fingerprint.dumpsys import focused_package

    assert focused_package("mCurrentFocus=Window{a u0 com.app/.MainActivity}") == "com.app"


def test_focused_package_systemui_shade():
    from wendle.fingerprint.dumpsys import focused_package

    assert focused_package("mCurrentFocus=Window{a u0 NotificationShade}") == "com.android.systemui"


def test_focused_package_none_when_unparseable():
    from wendle.fingerprint.dumpsys import focused_package

    assert focused_package("") is None
