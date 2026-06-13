from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3",
    abs_x=(0, 1079),
    abs_y=(0, 2339),
    display=(1080, 2340),
    touch_protocol="type_b",
)

NOSLEEP = {"sleep": lambda _dt: None}  # instant settle for already-settled FakeDriver scripts


def _screen(pkg, act, button_id="ok"):
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/{button_id}" '
        f'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/></node></hierarchy>'
    )


def _dumpsys(pkg, act):
    a = f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}"
    w = f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}"
    return (a, w)


def _settled_driver(*screens):
    """screens: list of (pkg, act). Each settles via 3 identical dumps."""
    hs, ds = [], []
    for pkg, act in screens:
        hs += [_screen(pkg, act)] * 3
        ds += [_dumpsys(pkg, act)] * 3
    return FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340))


def _tap(x, y):
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=x, y=y)


def test_start_enters_first_screen():
    drv = _settled_driver(("com.app", ".AActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    screen = s.start()
    assert screen.namespace == "com.app/.AActivity"
    assert screen.volatile is False
    assert s.current_id == screen.id


def test_records_transition_with_settled_and_landed():
    drv = _settled_driver(("com.app", ".AActivity"), ("com.app", ".BActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    t = s.record_gesture(_tap(540, 560))  # taps the button in screen A
    assert t is not None
    assert t.settled is True
    assert t.landed_on_real_element is True
    assert t.action.selector.kind == "label" and t.action.selector.value == "Go"
    # graph has A and B and an edge
    assert s.graph.g.number_of_nodes() == 2
    assert s.graph.g.number_of_edges() == 1


def test_multi_finger_gesture_records_nothing():
    drv = _settled_driver(("com.app", ".AActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    assert s.record_gesture(Gesture(kind="multi", t_down=1.0, t_up=1.05, x=540, y=560)) is None
    assert s.graph.g.number_of_edges() == 0


def test_launcher_gets_keyevent_anchor():
    drv = _settled_driver(("com.sec.android.app.launcher", ".activities.LauncherActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    screen = s.start()
    assert screen.force_action is not None
    assert screen.force_action.kind == "keyevent" and screen.force_action.value == "3"
    assert screen.force_action.verified
    assert s.graph.anchors() == [screen.id]


def test_app_entered_from_home_gets_am_start_anchor():
    # launcher start, then a tap into the app -> the app screen becomes an am_start anchor
    drv = _settled_driver(
        ("com.sec.android.app.launcher", ".activities.LauncherActivity"),
        ("com.app", ".MainActivity"),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 560))
    app = s.graph.screen(s.current_id)
    assert app.force_action.kind == "am_start"
    assert app.force_action.value == "com.app/.MainActivity"
    # reached an interactive screen DIRECTLY from the launcher (no splash) -> launcher_entry,
    # so the launch ladder tries the recorded component first.
    assert app.force_action.provenance == "launcher_entry"


def test_app_started_directly_has_no_anchor():
    # an app screen NOT entered from home gets no am_start anchor (avoids non-exported)
    drv = _settled_driver(("com.app", ".AActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    screen = s.start()
    assert screen.force_action is None


def test_never_settle_screen_is_volatile_node():
    # alternating structure -> never 3 consecutive identical -> settled=False
    a = _screen("com.feed", ".FeedActivity", "x")
    b = _screen("com.feed", ".FeedActivity", "y")  # different resource-id -> different sig
    ds = _dumpsys("com.feed", ".FeedActivity")
    drv = FakeDriver(hierarchies=[a, b] * 15, dumpsys_pairs=[ds] * 30, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs={"max_wait": 0.12, "interval": 0.01})
    screen = s.start()
    assert screen.volatile is True
    assert screen.id.startswith("V")
    assert screen.fingerprint_confidence == "low"


def test_provisional_edge_from_volatile_source():
    a = _screen("com.feed", ".FeedActivity", "x")
    b = _screen("com.feed", ".FeedActivity", "y")
    ds = _dumpsys("com.feed", ".FeedActivity")
    # first _enter volatile; then a settled target screen
    settled = [_screen("com.app", ".B")] * 3
    settled_ds = [_dumpsys("com.app", ".B")] * 3
    drv = FakeDriver(
        hierarchies=[a, b] * 10 + settled,
        dumpsys_pairs=[ds] * 20 + settled_ds,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs={"max_wait": 0.12, "interval": 0.01})
    s.start()
    t = s.record_gesture(_tap(540, 560))
    assert t.needs_confirmation is True  # tap out of a volatile screen is provisional
    assert s.provisional  # registered


def _splash():
    # a pure splash/loading screen: app-owned, but NO actionable affordance (just a label)
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.miapp" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            '<node class="android.widget.TextView" package="com.miapp" resource-id="com.miapp:id/loading" '
            'clickable="false" content-desc="" text="Cargando..." bounds="[300,1100][780,1180]"/></node></hierarchy>')


def test_launch_anchor_lands_on_first_interactive_screen_not_splash():
    # RULE 2: launcher -> splash (non-interactive) -> registry (interactive). The am_start anchor
    # must go on the REGISTRY (first real screen), NOT the splash; nothing is dropped.
    L = ("com.sec.android.app.launcher", ".activities.LauncherActivity")
    drv = FakeDriver(
        hierarchies=[_screen(*L)] * 3 + [_splash()] * 3 + [_screen("com.miapp", ".Registry", "go")] * 3,
        dumpsys_pairs=[_dumpsys(*L)] * 3 + [_dumpsys("com.miapp", ".Splash")] * 3
        + [_dumpsys("com.miapp", ".Registry")] * 3,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 560))  # launcher -> splash: anchor DEFERRED (no affordance)
    splash = s.graph.screen(s.current_id)
    assert splash.namespace == "com.miapp/.Splash" and splash.force_action is None
    s.record_gesture(_tap(540, 560))  # splash -> registry: anchor lands HERE
    real = s.graph.screen(s.current_id)
    assert real.namespace == "com.miapp/.Registry"
    assert real.force_action is not None and real.force_action.kind == "am_start"
    assert real.force_action.value == "com.miapp/.Registry"
    # the anchor was DEFERRED past the non-interactive splash -> self_routing, so the launch
    # ladder SKIPS the (likely non-exported) recorded component and lets the package default
    # route the splash in.
    assert real.force_action.provenance == "self_routing"
    # the splash node is still in the graph (faithful record) but carries no anchor
    splash_id = next(n for n in s.graph.g.nodes if s.graph.screen(n).namespace == "com.miapp/.Splash")
    assert s.graph.screen(splash_id).force_action is None


def test_abandoned_launch_resets_so_next_app_is_launcher_entry():
    # splash defer (_launching) then BACK OUT to the launcher: returning to the launcher
    # abandons the in-flight launch, so the NEXT app entered directly from the launcher is
    # launcher_entry. A sticky _launching used to mis-stamp it self_routing, making the
    # launch ladder skip the recorded component for an app that never had a splash.
    L = ("com.sec.android.app.launcher", ".activities.LauncherActivity")
    drv = FakeDriver(
        hierarchies=[_screen(*L)] * 3 + [_splash()] * 3 + [_screen(*L)] * 3
        + [_screen("com.otra", ".MainActivity")] * 3,
        dumpsys_pairs=[_dumpsys(*L)] * 3 + [_dumpsys("com.miapp", ".Splash")] * 3
        + [_dumpsys(*L)] * 3 + [_dumpsys("com.otra", ".MainActivity")] * 3,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 560))  # launcher -> splash: anchor deferred (_launching)
    s.record_gesture(_tap(540, 560))  # splash -> BACK on the launcher: launch abandoned
    s.record_gesture(_tap(540, 560))  # launcher -> com.otra (interactive, direct)
    otra = s.graph.screen(s.current_id)
    assert otra.namespace == "com.otra/.MainActivity"
    assert otra.force_action is not None
    assert otra.force_action.provenance == "launcher_entry"


def test_volatile_app_home_from_launcher_gets_am_start_anchor():
    # launcher -> a never-settle (volatile) app home -> still anchored via am_start
    L = _screen("com.sec.android.app.launcher", ".activities.LauncherActivity")
    Ldump = _dumpsys("com.sec.android.app.launcher", ".activities.LauncherActivity")
    a = _screen("com.spotify.music", ".MainActivity", "x")
    b = _screen("com.spotify.music", ".MainActivity", "y")  # alternates -> volatile
    sdump = _dumpsys("com.spotify.music", ".MainActivity")
    drv = FakeDriver(
        hierarchies=[L] * 3 + [a, b] * 12,
        dumpsys_pairs=[Ldump] * 3 + [sdump] * 24,
        display=(1080, 2340),
    )
    # real (tiny) sleep: launcher settles fast (3 identical), spotify feed never does
    s = RecordSession(drv, PROFILE, settle_kwargs={"max_wait": 0.12, "interval": 0.01})
    s.start()  # launcher
    s.record_gesture(_tap(540, 560))  # launcher -> spotify (volatile)
    spotify = s.graph.screen(s.current_id)
    assert spotify.volatile is True
    assert spotify.force_action is not None
    assert spotify.force_action.kind == "am_start"
    assert spotify.force_action.value == "com.spotify.music/.MainActivity"
