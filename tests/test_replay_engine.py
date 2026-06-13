"""Phase C — the ReplayEngine: per-command wait→act→verify→settle, honest-stop, redaction,
launch-by-package, coords reproduced, and the no-blind-sleep guard.
"""
import ast
import pathlib

from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.replay.engine import ReplayEngine

USER = ("resource_id", "app:id/user")
TERMS = ("resource_id", "app:id/terms")

# observe returns this minimal real hierarchy when the app foregrounds; each anchor screen's
# structure_id is computed from it so the launch gate's identity tier confirms the landing.
# These tests exercise ladder ORDERING / command flow; the identity tier itself is covered in
# tests/test_launch_gate.py.
LAUNCH_XML = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
              'content-desc=""><node class="android.widget.Button" resource-id="app:id/root" '
              'clickable="true" content-desc="" text="x"/></node></hierarchy>')


def _graph(action, pre_actions):
    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace="app/.A", package="app", activity=".A",
                           structure_id=structure_id("app/.A", LAUNCH_XML),
                           force_action=ForceAction("am_start", "app/.A", verified_fp="S0")))
    g.upsert_screen(Screen(id="S1", namespace="app/.B", package="app", activity=".B"))
    g.add_transition(Transition(source="S0", target="S1", action=action, pre_actions=pre_actions))
    return g


def _eng(graph, drv, **kw):
    t = [0.0]
    eng = ReplayEngine(graph, drv, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                       lookup_timeout=5.0, retry_timeout=1.0, **kw)
    eng._observe = lambda: (LAUNCH_XML, "app/.A", "app", True)  # skip settle machinery in unit tests
    return eng


_SET_TEXT = Action(selector=Selector(*USER), action_type="set_text", value={"text": "alice"})
_SET_CHK = Action(selector=Selector(*TERMS), action_type="set_checked", value={"checked": True})
_GO = Action(selector=Selector("text", "Continuar"), action_type="click")


def test_engine_and_navigator_share_one_ladder():
    # ONE ladder (one gate, one winning-rung cache) per engine: the cold launch seeds the
    # cache, and a hook-goto's re-force of the same anchor reuses the proven rung instead of
    # re-issuing a refused am-start's stop=True force-stop mid-replay. The shared instance is
    # the mechanism; the cache behavior itself is unit-covered in test_launch_ladder.py.
    g = _graph(_GO, [])
    eng = _eng(g, FakeDriver())
    assert eng._nav._ladder is eng._ladder
    assert eng._nav.clock is eng.clock and eng._nav.sleep is eng.sleep  # goto runs on the same fake time


def test_whole_flow_completes_with_launch_text_and_checkbox():
    g = _graph(_GO, [_SET_TEXT, _SET_CHK])
    drv = FakeDriver(present_selectors={USER, TERMS, ("text", "Continuar")})
    out = _eng(g, drv).run()
    assert out.status == "completed"
    # launch by the recorded ACTIVITY first (app/.A); text typed via focus_and_type, box flipped, tap
    assert ("app", ".A", True) in drv.app_starts and ("app", None, True) not in drv.app_starts
    assert drv.text_sets[-1] == ("resource_id", "app:id/user", "alice", "focus_and_type")
    assert drv.checked_sets and drv.checked_sets[0][:3] == ("resource_id", "app:id/terms", True)
    assert ("text", "Continuar", "click") in drv.taps


def test_honest_stop_on_missing_element():
    g = _graph(_GO, [_SET_TEXT, _SET_CHK])
    drv = FakeDriver(present_selectors={USER, TERMS})  # 'Continuar' never appears
    out = _eng(g, drv).run()
    assert out.status == "stopped"
    assert out.failed_step.action_type == "click" and out.failed_step.selector_kind == "text"
    assert "Continuar" not in repr(out)  # value-free
    assert ("text", "Continuar", "click") not in drv.taps  # never tapped a screen it couldn't confirm


def test_set_text_verify_mismatch_stops_honestly():
    g = _graph(_GO, [_SET_TEXT])
    drv = FakeDriver(present_selectors={USER, ("text", "Continuar")})
    drv.verify_fail.add(USER)  # the text doesn't actually land
    out = _eng(g, drv).run()
    assert out.status == "stopped"
    assert out.failed_step.action_type == "set_text" and out.failed_step.error == "text did not land"
    assert "alice" not in repr(out)


def test_redaction_no_value_in_output():
    sensitive = Action(selector=Selector("resource_id", "app:id/pw"), action_type="set_text",
                       value={"param": "pw"}, sensitive=True)
    g = _graph(_GO, [_SET_TEXT, sensitive])
    drv = FakeDriver(present_selectors={USER, ("resource_id", "app:id/pw"), ("text", "Continuar")})
    out = _eng(g, drv, params={"pw": "hunter2"}).run()
    assert out.status == "completed"
    blob = repr(out) + "".join(repr(s) for s in out.steps)
    assert "hunter2" not in blob and "alice" not in blob  # neither secret nor plain value leaks
    assert ("resource_id", "app:id/pw", "hunter2", "focus_and_type") in drv.text_sets  # but it WAS entered


def test_coords_tap_reproduced_low_confidence():
    coords_tap = Action(selector=Selector("coords", (500, 800)), action_type="click")
    g = _graph(coords_tap, [])
    drv = FakeDriver()  # coords need no present_selectors (nothing to wait on)
    out = _eng(g, drv).run()
    assert out.status == "completed"
    step = out.steps[-1]
    assert step.action_type == "click" and step.selector_kind == "coords" and step.low_confidence
    assert ("coords", (500, 800), "click") in drv.taps


def test_swipe_reproduced_with_endpoint():
    swipe = Action(selector=Selector("coords", (500, 1500)), action_type="swipe", end=(500, 400))
    g = _graph(swipe, [])
    out_drv = FakeDriver()
    out = _eng(g, out_drv).run()
    assert out.status == "completed"
    assert out_drv.swipes == [((500, 1500), (500, 400))]


def test_legacy_semantic_swipe_reconstructs_start_from_element_center():
    # An older recording stored a swipe's start as the element's LABEL + end coords. The engine
    # reconstructs the start from the element's center so the drag still replays (no re-record).
    swipe = Action(selector=Selector("content_desc", "List"), action_type="swipe", end=(540, 300))
    g = _graph(swipe, [])
    drv = FakeDriver(present_selectors={("content_desc", "List")})
    drv.element_centers[("content_desc", "List")] = (540, 1500)
    out = _eng(g, drv).run()
    assert out.status == "completed"
    assert drv.swipes == [((540, 1500), (540, 300))]


def test_credential_required_is_honest_and_names_param_not_secret():
    sensitive = Action(selector=Selector("resource_id", "app:id/pw"), action_type="set_text",
                       value={"param": "pw"}, sensitive=True)
    g = _graph(_GO, [sensitive])
    drv = FakeDriver(present_selectors={("resource_id", "app:id/pw"), ("text", "Continuar")})
    out = _eng(g, drv, params={}).run()  # no credential supplied
    assert out.status == "stopped" and out.failed_step.action_type == "set_text"
    assert out.failed_step.error == "credential required: pw"  # param NAME only


def _launch_graph(anchor_value):
    pkg, _, act = anchor_value.partition("/")
    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace=anchor_value, package=pkg, activity=act,
                           structure_id=structure_id(anchor_value, LAUNCH_XML),
                           force_action=ForceAction("am_start", anchor_value, verified_fp="S0")))
    g.upsert_screen(Screen(id="S1", namespace="x/.Next"))
    g.add_transition(Transition(source="S0", target="S1",
                                action=Action(selector=Selector("text", "Next"), action_type="click")))
    return g


GEMINI = ("com.google.android.googlequicksearchbox/"
          "com.google.android.apps.search.assistant.surfaces.voice.robin.main.MainActivity")
GPKG = "com.google.android.googlequicksearchbox"
LAUNCHER = "com.sec.android.app.launcher/.activities.LauncherActivity"


def test_shared_package_launches_by_activity_not_package():
    # Gemini is an ACTIVITY inside the Google app's package -> must launch the component, never
    # the package default (which is Google Search).
    g = _launch_graph(GEMINI)
    drv = FakeDriver(present_selectors={("text", "Next")})
    eng = _eng(g, drv)
    eng._observe = lambda: (LAUNCH_XML, GEMINI, GPKG, True)  # Gemini foregrounds
    out = eng.run()
    assert out.status == "completed"
    assert drv.app_starts[0] == (GPKG, GEMINI.partition("/")[2], True)  # launched by ACTIVITY
    assert (GPKG, None, True) not in drv.app_starts  # NEVER opened Google via the package default


def test_shared_package_wrong_surface_stops_honestly_never_opens_google():
    # the load-bearing trap: the recorded robin activity won't cold-launch (am_start leaves the
    # launcher); the package DEFAULT then foregrounds Google Search (SAME package, DIFFERENT
    # activity). The full-namespace verify rejects it and the ANTI-THRASH policy STOPS on that
    # real-but-wrong landing (wrong_surface) — never replaying onto Google, never thrashing to monkey.
    g = _launch_graph(GEMINI)
    drv = FakeDriver(present_selectors={("text", "Next")})
    eng = _eng(g, drv, activity_launch_timeout=1.0, launch_timeout=2.0)

    def _obs():
        if any(a[1] is None for a in drv.app_starts):  # package default issued -> Google foregrounds
            return ("<x/>", GPKG + "/.SearchActivity", GPKG, True)
        return ("<x/>", LAUNCHER, "com.sec.android.app.launcher", True)  # robin am_start: stuck on launcher

    eng._observe = _obs
    out = eng.run()
    assert out.status == "stopped" and out.failed_step.kind == "launch"
    assert out.failed_step.error == "wrong_surface"
    assert drv.app_starts == [(GPKG, GEMINI.partition("/")[2], True), (GPKG, None, True)]
    assert drv.monkey_launches == []                  # anti-thrash stopped before monkey
    assert ("text", "Next", "click") not in drv.taps  # never replayed onto the wrong app


def test_non_exported_activity_falls_back_to_package():
    # BanCoppel shape: the recorded activity is non-exported (never foregrounds), so the engine
    # falls back to a package launch that routes through the splash to the recorded screen.
    av = "mx.com.miapp/.ui.welcome.WelcomeActivity"
    g = _launch_graph(av)
    drv = FakeDriver(present_selectors={("text", "Next")})
    eng = _eng(g, drv, activity_launch_timeout=1.0, launch_timeout=3.0)

    def obs():
        if any(a[1] is None for a in drv.app_starts):  # package fallback issued -> app routes in
            return (LAUNCH_XML, av, "mx.com.miapp", True)
        return ("<x/>", LAUNCHER, "com.sec.android.app.launcher", True)  # activity attempt: stuck

    eng._observe = obs
    out = eng.run()
    assert out.status == "completed"
    assert drv.app_starts == [("mx.com.miapp", ".ui.welcome.WelcomeActivity", True),
                              ("mx.com.miapp", None, True)]


def test_shared_package_falls_back_to_recorded_icon_tap():
    # am start of the recorded activity can't reach Gemini (a launcher entry in the Google
    # package); the engine reproduces the recorded launcher 'Gemini' icon tap, which opens it.
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace="com.sec.android.app.launcher/.Home",
                           package="com.sec.android.app.launcher", activity=".Home",
                           force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(Screen(id="S0", namespace=GEMINI, package=GPKG, activity=GEMINI.partition("/")[2],
                           structure_id=structure_id(GEMINI, LAUNCH_XML),
                           force_action=ForceAction("am_start", GEMINI, verified_fp="S0")))
    g.upsert_screen(Screen(id="S1", namespace="x/.Next"))
    g.add_transition(Transition(source="L", target="S0",  # the recorded launcher icon tap
                                action=Action(selector=Selector("content_desc", "Gemini"), action_type="click")))
    g.add_transition(Transition(source="S0", target="S1",
                                action=Action(selector=Selector("text", "Next"), action_type="click")))
    drv = FakeDriver(present_selectors={("content_desc", "Gemini"), ("text", "Next")})
    eng = _eng(g, drv, activity_launch_timeout=1.0, launch_timeout=3.0)

    def obs():  # launcher until the 'Gemini' icon is tapped, then Gemini foregrounds
        if ("content_desc", "Gemini", "click") in drv.taps:
            return (LAUNCH_XML, GEMINI, GPKG, True)
        return ("<x/>", "com.sec.android.app.launcher/.Home", "com.sec.android.app.launcher", True)

    eng._observe = obs
    out = eng.run()
    assert out.status == "completed"
    assert out.steps[0].kind == "launch" and out.steps[0].ok  # launch succeeded
    assert 3 in drv.keyevents  # went home first
    assert ("content_desc", "Gemini", "click") in drv.taps  # reproduced the recorded icon tap
    assert (GPKG, None, True) not in drv.app_starts  # never package-launched Google Search


def test_wrong_same_activity_screen_is_caught_by_the_flow():
    # Maestro-style: a wrong same-activity screen (a login wall in the same single-Activity app)
    # PASSES the launch gate (namespace matches) — the honesty backstop is the FLOW. Its first
    # command waits for the recorded element, which the wrong screen LACKS, and STOPS HONESTLY.
    # The launch step itself succeeds; the engine never confidently replays onto the wrong screen.
    g = _launch_graph("app/.A")
    drv = FakeDriver()  # the recorded next element ("Next") is NOT present on the wrong screen
    eng = _eng(g, drv, activity_launch_timeout=1.0, launch_timeout=2.0)
    eng._observe = lambda: (LAUNCH_XML, "app/.A", "app", True)
    out = eng.run()
    assert out.status == "stopped"
    assert out.steps[0].kind == "launch" and out.steps[0].ok       # launch (namespace) succeeded
    assert out.failed_step.kind == "action"                         # the FLOW caught the wrong screen
    assert ("text", "Next", "click") not in drv.taps                # never tapped on the wrong screen


def test_launch_total_failure_stops_honestly():
    g = _launch_graph("com.x/.Main")
    drv = FakeDriver()
    eng = _eng(g, drv, activity_launch_timeout=1.0, launch_timeout=2.0)
    eng._observe = lambda: ("<x/>", LAUNCHER, "com.sec.android.app.launcher", True)  # never foregrounds
    out = eng.run()
    assert out.status == "stopped" and out.failed_step.kind == "launch"
    assert drv.app_starts == [("com.x", ".Main", True), ("com.x", None, True)]


def test_multi_app_flow_relaunches_each_app_not_home_gestures():
    # The on-device finding: a multi-app recording (Home -> app A -> Home -> app B) must RE-LAUNCH
    # each app at its boundary, NOT replay the fragile launcher gestures used to open it (tapping
    # the home search bar, typing the app name, tapping the icon — the typing often isn't even
    # captured). app A is launched by the engine; at the A->Home->B boundary the flow launches B
    # via its anchor and drops the home swipe + the 'open B' icon tap.
    L = "com.sec.android.app.launcher/.Home"
    A, B = "com.a/.Main", "com.b/.Main"
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace=L, package="com.sec.android.app.launcher", activity=".Home",
                           force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(Screen(id="A", namespace=A, package="com.a", activity=".Main",
                           force_action=ForceAction("am_start", A, verified_fp="A")))
    g.upsert_screen(Screen(id="A2", namespace=A, package="com.a", activity=".Main"))
    g.upsert_screen(Screen(id="B", namespace=B, package="com.b", activity=".Main",
                           force_action=ForceAction("am_start", B, verified_fp="B")))
    g.add_transition(Transition(source="L", target="A",  # opened A from home (icon tap)
                                action=Action(selector=Selector("content_desc", "AppA"), action_type="click")))
    g.add_transition(Transition(source="A", target="A2",  # in-app tap
                                action=Action(selector=Selector("text", "tabA"), action_type="click")))
    g.add_transition(Transition(source="A2", target="L",  # swiped back to home
                                action=Action(selector=Selector("coords", (500, 2500)), action_type="swipe", end=(500, 100))))
    g.add_transition(Transition(source="L", target="B",  # opened B from home (icon tap)
                                action=Action(selector=Selector("content_desc", "AppB"), action_type="click")))
    drv = FakeDriver(present_selectors={("text", "tabA")})
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                       lookup_timeout=5.0, retry_timeout=1.0, activity_launch_timeout=1.0, launch_timeout=2.0)

    def obs():  # foreground namespace tracks the most-recently-launched app
        last = drv.app_starts[-1][0] if drv.app_starts else None
        if last == "com.b":
            return ("<x/>", B, "com.b", True)
        if last == "com.a":
            return ("<x/>", A, "com.a", True)
        return ("<x/>", L, "com.sec.android.app.launcher", True)

    eng._observe = obs
    out = eng.run()
    assert out.status == "completed"
    assert ("com.a", ".Main", True) in drv.app_starts          # app A launched
    assert ("com.b", ".Main", True) in drv.app_starts          # app B RE-LAUNCHED at the boundary
    assert ("text", "tabA", "click") in drv.taps               # the in-app tap WAS replayed
    assert ("content_desc", "AppA", "click") not in drv.taps   # home->A icon tap dropped (launched instead)
    assert ("content_desc", "AppB", "click") not in drv.taps   # home->B icon tap dropped (re-launched)
    assert drv.swipes == []                                     # the swipe-home gesture dropped


def test_no_blind_sleep_in_replay_package():
    pkg = pathlib.Path(__file__).resolve().parent.parent / "wendle" / "replay"
    for src in pkg.glob("*.py"):
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                is_sleep = (isinstance(f, ast.Attribute) and f.attr == "sleep"
                            and isinstance(f.value, ast.Name) and f.value.id == "time")
                assert not is_sleep, f"blind time.sleep in replay/{src.name}"
