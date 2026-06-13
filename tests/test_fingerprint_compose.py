from wendle.fingerprint.compose import (
    compose_config,
    is_compose_dominant,
    select_config,
)

COMPOSE = """<hierarchy>
 <node class="androidx.compose.ui.platform.AndroidComposeView" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">
  <node class="android.view.View" resource-id="" clickable="true" content-desc="Home" text="" bounds="[0,0][100,100]"/>
  <node class="android.view.View" resource-id="" clickable="true" content-desc="Search" text="" bounds="[100,0][200,100]"/>
  <node class="android.view.View" resource-id="" clickable="true" content-desc="Profile" text="" bounds="[200,0][300,100]"/>
 </node>
</hierarchy>"""

VIEW_BASED = """<hierarchy>
 <node class="android.widget.FrameLayout" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">
  <node class="android.widget.Button" resource-id="com.app:id/a" clickable="true" content-desc="" text="A" bounds="[0,0][100,100]"/>
  <node class="android.widget.Button" resource-id="com.app:id/b" clickable="true" content-desc="" text="B" bounds="[100,0][200,100]"/>
 </node>
</hierarchy>"""


def test_detects_compose_dominant():
    assert is_compose_dominant(COMPOSE) is True


def test_view_based_is_not_compose():
    assert is_compose_dominant(VIEW_BASED) is False


def test_select_config_promotes_text_for_compose():
    assert select_config(COMPOSE).include_text is True
    assert select_config(VIEW_BASED).include_text is False


def test_compose_config_promotes_text():
    assert compose_config().include_text is True


def test_compose_dominant_with_status_bar_present():
    xml = COMPOSE.replace(
        "</node>\n</hierarchy>",
        '<node class="android.view.View" resource-id="com.android.systemui:id/status_bar" clickable="false" content-desc="" text="" bounds="[0,0][1080,80]"/></node></hierarchy>',
    )
    assert is_compose_dominant(xml) is True  # decor leaf excluded from denominator


def test_custom_composeview_wrapper_is_not_a_host():
    xml = '<hierarchy><node class="com.app.MyComposeViewWrapper" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][10,10]"><node class="android.view.View" resource-id="" clickable="true" content-desc="x" text="" bounds="[0,0][5,5]"/></node></hierarchy>'
    assert is_compose_dominant(xml) is False


def test_launcher_namespace_uses_shallow_profile():
    from wendle.fingerprint.compose import (
        LAUNCHER_PROFILE,
        is_launcher_namespace,
        resolve_profile,
    )

    assert is_launcher_namespace("com.sec.android.app.launcher/.activities.LauncherActivity")
    assert is_launcher_namespace("com.google.android.apps.nexuslauncher/.NexusLauncherActivity")
    assert is_launcher_namespace("com.miui.home/.launcher.Launcher")
    # an app's own HomeActivity must NOT be treated as the launcher
    assert not is_launcher_namespace("com.whatsapp/.home.ui.HomeActivity")
    cfg = resolve_profile(VIEW_BASED, "com.sec.android.app.launcher/.activities.LauncherActivity")
    assert cfg is LAUNCHER_PROFILE


def test_home_pages_collapse_to_one_shallow_id():
    from wendle.fingerprint.compose import LAUNCHER_PROFILE
    from wendle.fingerprint.signature import fingerprint

    # two home pages: identical shallow skeleton, different DEEP icon subtrees
    def home(page_icons):
        deep = "".join(
            f'<node class="android.widget.TextView" resource-id="com.sec.android.app.launcher:id/icon_{i}" clickable="true" content-desc="" text="" bounds="[0,0][100,100]"/>'
            for i in page_icons
        )
        return (
            '<hierarchy><node class="DragLayer" resource-id="com.sec.android.app.launcher:id/drag_layer" clickable="false" content-desc="" text="" bounds="[0,0][1080,2400]">'
            '<node class="Workspace" resource-id="com.sec.android.app.launcher:id/workspace" clickable="false" content-desc="" text="" bounds="[0,0][1080,2200]">'
            f'<node class="CellLayout" resource-id="com.sec.android.app.launcher:id/page" clickable="false" content-desc="" text="" bounds="[0,0][1080,2200]">{deep}</node>'
            "</node></node></hierarchy>"
        )

    ns = "com.sec.android.app.launcher/.activities.LauncherActivity"
    a = fingerprint(ns, home([1, 2, 3]), LAUNCHER_PROFILE)
    b = fingerprint(ns, home([4, 5, 6, 7]), LAUNCHER_PROFILE)
    assert a == b  # different icons/pages -> same shallow home id
