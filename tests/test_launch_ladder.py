"""The shared LaunchLadder — the ONE ordered list of pluggable launch strategies behind ONE
verify_foreground gate (replaces the inline am_start chains). Covers: HomePress is a gate-exempt
mutually-exclusive branch; a raising rung advances the ladder; launch_tap is anchor-scoped for
multi-app; and the new module honors the no-blind-sleep invariant.
"""
import ast
import pathlib

from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import structure_id
from wendle.graph import Graph
from wendle.launch import LaunchLadder
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.replay.commands import flow_from_recording, launch_tap

LAUNCHER_NS = "com.sec.android.app.launcher/.Home"

# observe returns this minimal real hierarchy when the app "foregrounds"; an anchor screen's
# structure_id is computed from the SAME xml so the gate's structure-identity tier confirms it.
# (These tests exercise ladder ORDERING; the gate's identity tier is covered in test_launch_gate.py.)
LAUNCH_XML = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
              'content-desc=""><node class="android.widget.Button" resource-id="app:id/root" '
              'clickable="true" content-desc="" text="x"/></node></hierarchy>')


def _seen(ns, focus=None):
    return (LAUNCH_XML, ns, focus or ns.split("/")[0], True)


def _anchor_screen(sid, ns, **kw):
    pkg = ns.split("/")[0]
    act = ns.split("/", 1)[1] if "/" in ns else ""
    return Screen(id=sid, namespace=ns, structure_id=structure_id(ns, LAUNCH_XML),
                  package=pkg, activity=act, **kw)


def _ladder(graph, drv, obs, **kw):
    t = [0.0]
    return LaunchLadder(graph, drv, obs, clock=lambda: t[0],
                        sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                        activity_launch_timeout=1.0, launch_timeout=3.0, **kw)


def test_homepress_is_terminal_deferred_and_gate_exempt():
    # A launcher-keyevent anchor (home-start recording) is NOT an am_start rung: press HOME,
    # hand readiness to the caller's next step. It must NOT run the namespace gate (it lands on
    # the launcher, which the gate rejects) and must NOT try any app_start.
    drv = FakeDriver()
    res = _ladder(Graph(), drv, lambda: ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)) \
        .launch(ForceAction("keyevent", "3", verified_fp="L"))
    assert res.landed and res.deferred
    assert drv.keyevents == [3] and drv.app_starts == []


def test_recorded_component_raise_advances_to_package_default():
    # `am start -n` of a non-exported component is refused (raises). The ladder must CATCH that
    # at the ladder level and advance to the next rung — the gate, not an exit code, is the sole
    # arbiter of landed. Here package-default routes the splash in.
    g = Graph()  # no launcher edge -> icon_tap rung is skipped
    g.upsert_screen(_anchor_screen("S0", "mx.app/.Welcome"))
    drv = FakeDriver()
    drv.app_start_raises.add(("mx.app", ".Welcome"))  # recorded component non-exported

    def obs():
        if any(a[1] is None for a in drv.app_starts):  # package-default issued -> app routes in
            return _seen("mx.app/.Welcome")
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    res = _ladder(g, drv, obs).launch(ForceAction("am_start", "mx.app/.Welcome", verified_fp="S0"))
    assert res.landed
    assert ("mx.app", ".Welcome", True) in drv.app_starts  # the refused attempt was made
    assert ("mx.app", None, True) in drv.app_starts        # then advanced to package default


def test_total_failure_is_honest_not_landed():
    g = Graph()
    drv = FakeDriver()
    res = _ladder(g, drv, lambda: ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)) \
        .launch(ForceAction("am_start", "com.x/.Main", verified_fp="S0"))
    assert not res.landed and res.error == "app did not foreground"
    assert drv.app_starts == [("com.x", ".Main", True), ("com.x", None, True)]  # tried, then exhausted


def test_landed_result_carries_the_gates_observation():
    # The gate just observed the foregrounded app; callers (the navigator's loop) must be able
    # to consume THAT observation instead of re-observing — on device it halves post-launch
    # observes, in tests it keeps FakeDriver frame-accounting exact.
    g = Graph()
    g.upsert_screen(_anchor_screen("S0", "mx.app/.Welcome"))
    drv = FakeDriver()
    res = _ladder(g, drv, lambda: _seen("mx.app/.Welcome")).launch(
        ForceAction("am_start", "mx.app/.Welcome", verified_fp="S0"))
    assert res.landed
    assert res.observation == _seen("mx.app/.Welcome")  # the (xml, ns, focus, settled) tuple


def test_deferred_and_exhausted_results_have_no_observation():
    drv = FakeDriver()
    home = _ladder(Graph(), drv, lambda: _seen(LAUNCHER_NS)).launch(
        ForceAction("keyevent", "3", verified_fp="L"))
    assert home.deferred and home.observation is None  # gate-exempt: nothing was verified
    lost = _ladder(Graph(), drv, lambda: ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)) \
        .launch(ForceAction("am_start", "com.x/.Main", verified_fp="S0"))
    assert not lost.landed and lost.observation is None


GEM = "com.google.android.googlequicksearchbox/.GeminiAlias"
KEEP = "com.google.android.keep/.KeepActivity"


def _multi_app_graph():
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace=LAUNCHER_NS, package="com.sec.android.app.launcher",
                           activity=".Home", force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(_anchor_screen("G", GEM))
    g.upsert_screen(_anchor_screen("K", KEEP))
    # L -> Gemini recorded FIRST, L -> Keep second (so 'first launcher edge' is Gemini)
    g.add_transition(Transition(source="L", target="G",
                                action=Action(selector=Selector("content_desc", "Gemini"), action_type="click")))
    g.add_transition(Transition(source="L", target="K",
                                action=Action(selector=Selector("content_desc", "Keep"), action_type="click")))
    return g


def test_launch_tap_is_anchor_scoped_for_multi_app():
    g = _multi_app_graph()
    gem = ForceAction("am_start", GEM, verified_fp="G")
    keep = ForceAction("am_start", KEEP, verified_fp="K")
    # the icon that opens THIS app — Keep's anchor must NOT resolve the first (Gemini) edge
    assert launch_tap(g, gem)[0].selector.value == "Gemini"
    assert launch_tap(g, keep)[0].selector.value == "Keep"
    # back-compat: no anchor -> the first launcher edge in the graph
    assert launch_tap(g)[0].selector.value == "Gemini"
    # an anchor for an app with no recorded launcher edge -> None (no icon to reproduce)
    assert launch_tap(g, ForceAction("am_start", "com.unknown/.X", verified_fp="U")) is None


def test_icon_tap_rung_uses_the_anchors_own_icon():
    # In the ladder, the IconTap rung must tap the icon for the anchor being launched — Keep's
    # icon for Keep, even though Gemini's launcher edge was recorded first.
    g = _multi_app_graph()
    drv = FakeDriver(present_selectors={("content_desc", "Keep")})

    def obs():
        if ("content_desc", "Keep", "click") in drv.taps:
            return _seen(KEEP)
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    # Keep's recorded component is non-exported here (raises) so the ladder reaches IconTap.
    drv.app_start_raises.add((KEEP.split("/")[0], ".KeepActivity"))
    res = _ladder(g, drv, obs).launch(ForceAction("am_start", KEEP, verified_fp="K"))
    assert res.landed
    assert ("content_desc", "Keep", "click") in drv.taps          # tapped Keep's icon
    assert ("content_desc", "Gemini", "click") not in drv.taps    # NOT the first launcher edge


def test_winning_rung_cache_skips_refused_component_on_relaunch():
    # Re-launching the SAME anchor (same verified_fp) must try the PROVEN rung first: the
    # refused `am start -n` — whose stop=True force-stop kills the whole shared package's
    # state (Search+Gemini) — is never re-issued on a restart.
    g = _multi_app_graph()
    drv = FakeDriver(present_selectors={("content_desc", "Keep")})

    def obs():
        if ("content_desc", "Keep", "click") in drv.taps:
            return _seen(KEEP)
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    drv.app_start_raises.add((KEEP.split("/")[0], ".KeepActivity"))
    ladder = _ladder(g, drv, obs)
    anchor = ForceAction("am_start", KEEP, verified_fp="K")
    assert ladder.launch(anchor).landed
    assert len(drv.app_starts) == 1          # the one refused component attempt
    drv.taps.clear()                          # re-arm obs() for the second launch
    assert ladder.launch(anchor).landed       # relaunch: cache -> icon_tap directly
    assert len(drv.app_starts) == 1           # NO new am_start: no force-stop on restart
    assert ("content_desc", "Keep", "click") in drv.taps


def test_cache_falls_back_to_full_ladder_when_winning_rung_stops_working():
    g = _multi_app_graph()
    drv = FakeDriver(present_selectors={("content_desc", "Keep")})

    def obs():
        if ("content_desc", "Keep", "click") in drv.taps or any(a[1] is None for a in drv.app_starts):
            return _seen(KEEP)
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    drv.app_start_raises.add((KEEP.split("/")[0], ".KeepActivity"))
    ladder = _ladder(g, drv, obs)
    anchor = ForceAction("am_start", KEEP, verified_fp="K")
    assert ladder.launch(anchor).landed                      # wins via icon_tap
    drv.taps.clear()
    drv.present_selectors.discard(("content_desc", "Keep"))  # icon gone (launcher reshuffle)
    assert ladder.launch(anchor).landed                      # falls back -> package_default lands
    assert ("com.google.android.keep", None, True) in drv.app_starts


def test_self_routing_provenance_skips_recorded_component():
    # A self_routing anchor (recorder deferred it past a splash -> recorded activity is a deep,
    # likely non-exported surface) must SKIP the doomed `am start -n <activity>` and go straight
    # to the package default, saving a guaranteed-refused call + its wasted poll.
    g = Graph()
    g.upsert_screen(_anchor_screen("S0", "mx.app/.Welcome"))
    drv = FakeDriver()

    def obs():
        return _seen("mx.app/.Welcome") if drv.app_starts \
            else ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    anchor = ForceAction("am_start", "mx.app/.Welcome", verified_fp="S0", provenance="self_routing")
    res = _ladder(g, drv, obs).launch(anchor)
    assert res.landed
    assert drv.app_starts == [("mx.app", None, True)]  # ONLY the package default; the activity was never tried


def test_monkey_rescues_non_exported_single_entry():
    # `am start -n` is refused (recorded component raises) AND package_default's am-start of the
    # resolved mainActivity doesn't foreground; monkey's LAUNCHER-category launch resolves the
    # entry (bypasses the per-component exported check) and lands it. Last rung before exhaustion.
    g = Graph()  # no launcher edge -> icon_tap skipped
    g.upsert_screen(_anchor_screen("S0", "solo.app/.Main"))
    drv = FakeDriver()
    drv.app_start_raises.add(("solo.app", ".Main"))

    def obs():
        if drv.monkey_launches:  # only monkey reaches it
            return _seen("solo.app/.Main")
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    res = _ladder(g, drv, obs).launch(ForceAction("am_start", "solo.app/.Main", verified_fp="S0"))
    assert res.landed
    assert drv.monkey_launches == ["solo.app"]
    assert ("solo.app", None, True) in drv.app_starts  # package_default was tried before monkey


def test_force_action_provenance_round_trips_through_json():
    from wendle.models import Screen

    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace="mx.app/.Welcome", package="mx.app", activity=".Welcome",
                           force_action=ForceAction("am_start", "mx.app/.Welcome", verified_fp="S0",
                                                     provenance="self_routing")))
    g2 = Graph.from_json(g.to_json())
    assert g2.screen("S0").force_action.provenance == "self_routing"


def test_wrong_surface_landing_stops_ladder_no_thrash():
    # ANTI-THRASH: a rung that lands a REAL but WRONG app (a multi-entry shared package — Google's
    # .SearchActivity, not Gemini) must STOP the ladder honestly (wrong_surface), NOT advance to
    # monkey which would open the wrong app AGAIN. Was: kept thrashing through every rung.
    g = Graph()  # no launcher edge -> icon_tap skipped, straight to package_default
    g.upsert_screen(_anchor_screen("G", GEM))
    drv = FakeDriver()
    WRONG = "com.google.android.googlequicksearchbox/.SearchActivity"  # the package default = Google Search
    def obs():
        if any(a[1] is None for a in drv.app_starts):  # package_default issued -> wrong app foregrounds
            return _seen(WRONG)
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)
    res = _ladder(g, drv, obs).launch(ForceAction("am_start", GEM, verified_fp="G"))
    assert not res.landed and res.error == "wrong_surface"
    assert drv.monkey_launches == []  # STOPPED before monkey -> no thrash into another wrong app


def test_wrong_activity_after_component_still_tries_the_recorded_icon():
    # ANTI-THRASH IS MECHANISM-SCOPED (review finding 11): a wrong surface condemns only rungs
    # sharing the failed rung's RESOLUTION MECHANISM. `am start -n` landing mx.app/.Home instead
    # of the recorded mx.app/.Splash (the app self-routed) says nothing about the recorded icon
    # GESTURE, which is how the recording actually entered the app. Was: a hard wrong_surface
    # stop after the first rung, before the faithful mechanism ever ran.
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace=LAUNCHER_NS, package="com.sec.android.app.launcher",
                           activity=".Home", force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(_anchor_screen("S0", "mx.app/.Splash"))
    g.add_transition(Transition(source="L", target="S0",
                                action=Action(selector=Selector("content_desc", "MiApp"), action_type="click")))
    drv = FakeDriver(present_selectors={("content_desc", "MiApp")})

    def obs():
        if ("content_desc", "MiApp", "click") in drv.taps:  # the recorded gesture reproduces the entry
            return _seen("mx.app/.Splash")
        if drv.app_starts:  # the component launched, but the app routed to a DIFFERENT surface
            return _seen("mx.app/.Home")
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    res = _ladder(g, drv, obs).launch(ForceAction("am_start", "mx.app/.Splash", verified_fp="S0"))
    assert res.landed
    assert ("content_desc", "MiApp", "click") in drv.taps  # the icon rung RAN after the wrong surface


def test_icon_tap_skips_gracefully_when_icon_unreachable():
    # ICON-REACH: if the app's launcher icon is NOT on the home page HOME lands on, the icon_tap
    # rung must WAIT for it and SKIP (return None) when it never appears — never tap a phantom /
    # fall through to a wrong app. Was: resolve_and_tap fired blindly on the absent icon.
    g = _multi_app_graph()  # has the L -> Gemini icon edge
    drv = FakeDriver()  # 'Gemini' icon NOT present -> unreachable after HOME
    drv.app_start_raises.add((GEM.split("/")[0], ".GeminiAlias"))  # recorded_component raises -> reach icon_tap
    res = _ladder(g, drv, lambda: ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)) \
        .launch(ForceAction("am_start", GEM, verified_fp="G"))
    assert drv.keyevents == [3]                                   # HOME was pressed (icon_tap reached)
    assert ("content_desc", "Gemini", "click") not in drv.taps    # but the absent icon was NOT tapped


def test_reentry_without_target_anchor_relaunches_via_package_anchor():
    # RE-ENTRY: re-entering app A at a screen with NO anchor (A3) — after A1(anchored)->B->A3 — must
    # re-LAUNCH A via A1's package anchor, not replay the fragile recorded back-gesture.
    A, B = "com.a/.Main", "com.b/.Main"
    g = Graph()
    g.upsert_screen(Screen(id="A1", namespace=A, package="com.a", activity=".Main",
                           force_action=ForceAction("am_start", A, verified_fp="A1")))
    g.upsert_screen(Screen(id="A2", namespace=A, package="com.a", activity=".Main"))
    g.upsert_screen(Screen(id="B", namespace=B, package="com.b", activity=".Main",
                           force_action=ForceAction("am_start", B, verified_fp="B")))
    g.upsert_screen(Screen(id="A3", namespace=A, package="com.a", activity=".Main"))  # NO anchor
    click = lambda v: Action(selector=Selector("text", v), action_type="click")
    g.add_transition(Transition(source="A1", target="A2", action=click("x")))
    g.add_transition(Transition(source="A2", target="B", action=click("y")))
    g.add_transition(Transition(source="B", target="A3",
                                action=Action(selector=Selector("keyevent", "4"), action_type="keyevent")))
    launches = [c for c in flow_from_recording(g, start_id="A1") if c.kind == "launch"]
    assert [c.anchor.value for c in launches] == [B, A]  # B launched, then A RE-launched on re-entry


def test_no_blind_sleep_in_launch_module():
    src = pathlib.Path(__file__).resolve().parent.parent / "wendle" / "launch.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            is_sleep = (isinstance(f, ast.Attribute) and f.attr == "sleep"
                        and isinstance(f.value, ast.Name) and f.value.id == "time")
            assert not is_sleep, "blind time.sleep in launch.py — waits must use injectable sleep"


def test_homepress_with_a_malformed_key_code_is_honest_not_a_crash():
    # GAP #1 (completeness audit): a keyevent anchor whose value is not an integer (corrupted /
    # legacy / cross-tool-ingested recording) must return a TYPED not-landed, NEVER raise out of
    # launch(). Honesty-first #1: stop-and-report on bad data, never crash. App-agnostic rule: a
    # launch anchor with an uncoercible key code is an honest not-landed. Mirrors actions._keyevent.
    drv = FakeDriver()
    res = _ladder(Graph(), drv, lambda: ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)) \
        .launch(ForceAction("keyevent", "not-an-int", verified_fp="L"))
    assert not res.landed
    assert res.error == "malformed keyevent anchor"
    assert drv.keyevents == []  # a bogus key code is never dispatched


def test_icon_tap_rung_skips_honestly_on_a_malformed_home_keycode():
    # GAP #1 sibling (adversarial): the icon-tap rung presses HOME before tapping the icon. A
    # malformed HOME keycode in the launcher anchor must SKIP the rung (return None -> the ladder
    # advances), never crash on keyevent(int('not-an-int')). Same app-agnostic rule as HomePress.
    g = _multi_app_graph()
    g.screen("L").force_action = ForceAction("keyevent", "not-an-int", verified_fp="L")
    drv = FakeDriver(present_selectors={("content_desc", "Keep")})

    def obs():
        if ("content_desc", "Keep", "click") in drv.taps:
            return _seen(KEEP)
        return ("<x/>", LAUNCHER_NS, "com.sec.android.app.launcher", True)

    drv.app_start_raises.add((KEEP.split("/")[0], ".KeepActivity"))  # push the ladder past am_start
    res = _ladder(g, drv, obs).launch(ForceAction("am_start", KEEP, verified_fp="K"))
    assert not res.landed                                       # honest not-landed, no crash
    assert ("content_desc", "Keep", "click") not in drv.taps   # rung skipped before the icon tap
