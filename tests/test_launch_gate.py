"""The launch gate (Maestro-style `launchApp` + per-command waits): confirm the recorded
app+ACTIVITY foregrounded, then let the FLOW verify screen content. Screen-content identity is
NOT gated at launch — app homes are dynamic (Settings suggestion cards, feeds, greetings) and the
recorder may hold both a volatile and a settled node for one screen, so a fingerprint/structure
gate OVER-REJECTS correct launches (it made a plain Settings replay re-open the app on every rung
on-device). The wrong-screen honesty backstop is the flow's per-command element wait — see
tests/test_replay_engine.py::test_wrong_same_activity_screen_is_caught_by_the_flow.
"""
from wendle.driver.fake import FakeDriver
from wendle.graph import Graph
from wendle.launch import LaunchLadder
from wendle.models import ForceAction

NS = "com.bank/.HomeActivity"
GPKG = "com.google.android.googlequicksearchbox"
GEMINI = GPKG + "/.robin.MainActivity"
LAUNCHER = "com.sec.android.app.launcher/.Home"


def _ladder(obs):
    drv = FakeDriver()
    t = [0.0]
    ladder = LaunchLadder(Graph(), drv, obs, clock=lambda: t[0],
                          sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                          activity_launch_timeout=1.0, launch_timeout=2.0)
    return ladder, drv


def test_lands_on_full_namespace_match():
    ladder, _ = _ladder(lambda: ("<x/>", NS, "com.bank", True))
    assert ladder.launch(ForceAction("am_start", NS, verified_fp="s")).landed


def test_dynamic_home_lands_despite_changed_content():
    # THE Settings fix: a launch must NOT be rejected because the home's content changed since
    # record (suggestion cards, a feed, a greeting). Namespace matches -> landed; the flow verifies
    # content. A fingerprint/structure-identity gate used to reject this and re-open the app forever.
    ladder, _ = _ladder(lambda: ("<totally-different-xml/>", NS, "com.bank", True))
    assert ladder.launch(ForceAction("am_start", NS, verified_fp="s")).landed


def test_rejects_launcher_namespace():
    ladder, _ = _ladder(lambda: ("<x/>", LAUNCHER, "com.sec.android.app.launcher", True))
    assert not ladder.launch(ForceAction("am_start", NS, verified_fp="s")).landed


def test_shared_package_wrong_surface_stops_honestly_not_landed_on_google():
    # Gemini's anchor is the robin activity; a rung foregrounds SearchActivity (SAME package,
    # DIFFERENT activity = Google Search). The full-namespace (not package-only) gate rejects it,
    # and the ANTI-THRASH policy STOPS honestly on that real-but-wrong landing (wrong_surface) —
    # never confidently replaying onto Google, never thrashing through the remaining rungs.
    ladder, drv = _ladder(lambda: ("<x/>", GPKG + "/.SearchActivity", GPKG, True))
    res = ladder.launch(ForceAction("am_start", GEMINI, verified_fp="s"))
    assert not res.landed and res.error == "wrong_surface"
    assert drv.monkey_launches == []  # stopped on the first wrong surface, not at exhaustion
