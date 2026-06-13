"""Regression tests for the Spike 3 adversarial-review findings."""
import hashlib

import pytest

from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.compose import COMPOSE_PROFILE
from wendle.fingerprint.signature import (
    adapter_list_dominant,
    fingerprint,
    structure_id,
)
from wendle.graph import Graph
from wendle.models import (
    Action,
    DeviceProfile,
    ForceAction,
    Screen,
    Selector,
    Transition,
)
from wendle.navigate.navigator import navigate
from wendle.navigate.verify import Tier, verify_match
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
NOSLEEP = {"sleep": lambda _dt: None}


# ---------- #1/#2/#4: structure_id collision must never produce a CONFIDENT arrival ----------

def _compose(rid, text, ns_pkg="com.app"):
    return (
        '<hierarchy><node class="androidx.compose.ui.platform.AndroidComposeView" '
        f'package="{ns_pkg}" resource-id="" clickable="false" content-desc="" text="" '
        'bounds="[0,0][1080,2340]">'
        f'<node class="android.view.View" package="{ns_pkg}" resource-id="{ns_pkg}:id/{rid}" '
        f'clickable="true" content-desc="" text="{text}" bounds="[40,500][1040,620]"/>'
        "</node></hierarchy>"
    )


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


@pytest.mark.parametrize("settings_first", [True, False])
def test_structural_twin_never_confident_arrival(settings_first):
    # Two Compose siblings in ONE activity sharing a widget skeleton -> same structure_id,
    # different EXACT (text). Standing on the sibling while observing dynamic content that
    # won't reproduce EXACT must NEVER yield a confident `arrived` (it could be either
    # twin) — and the result must not depend on graph insertion order.
    ns = "com.app/.MainActivity"
    settings_xml = _compose("item", "Settings")
    profile_xml = _compose("item", "Profile")
    observed = _compose("item", "Profile (live)")  # dynamic: matches structure, not EXACT
    settings = Screen(
        id=fingerprint(ns, settings_xml, COMPOSE_PROFILE), namespace=ns,
        structure_id=structure_id(ns, settings_xml), package="com.app",
        activity=".MainActivity", profile_name="compose",
    )
    profile = Screen(
        id=fingerprint(ns, profile_xml, COMPOSE_PROFILE), namespace=ns,
        structure_id=structure_id(ns, profile_xml), package="com.app",
        activity=".MainActivity", profile_name="compose",
    )
    assert settings.structure_id == profile.structure_id  # the collision premise
    assert settings.id != profile.id

    splash_ns = "com.app/.SplashActivity"
    splash = Screen(
        id="splash", namespace=splash_ns, package="com.app", activity=".SplashActivity",
        force_action=ForceAction("am_start", "com.app/.SplashActivity", verified_fp="splash"),
    )
    g = Graph()
    order = [settings, profile] if settings_first else [profile, settings]
    g.upsert_screen(splash)
    for s in order:
        g.upsert_screen(s)

    drv = FakeDriver(
        hierarchies=[observed] * 3,
        dumpsys_pairs=[_dumpsys(ns)] * 3,
        present_selectors={("resource_id", "com.app:id/item")},
        display=(1080, 2340),
    )
    out = navigate(g, splash.id, settings.id, drv, settle_kwargs=NOSLEEP)
    # honest: plausibly there but unprovable — NOT a confident "arrived"
    assert out.status == "arrived_unverified", out.status


def test_structural_twin_intermediate_still_routes_to_target():
    # review-2 #A: a twin INTERMEDIATE that has a real recorded route to the target must
    # still be walked (not prematurely give up at arrived_unverified). I and T share a
    # structure_id, I's live content no longer reproduces I's EXACT fp, and there is a
    # recorded I -> T edge.
    ns = "com.app/.MainActivity"
    i_xml = _compose("item", "Feed v1")
    t_xml = _compose("item", "Detail")
    i_live = _compose("item", "Feed v2")  # dynamic: structure matches I/T, EXACT matches neither
    from wendle.fingerprint.signature import outside_region_value_bearing

    # L3 migration: these compose ids fold in real text values outside any region, so the
    # recorder would stamp value_bearing=True — computed here the same way, not asserted.
    intermediate = Screen(
        id=fingerprint(ns, i_xml, COMPOSE_PROFILE), namespace=ns,
        structure_id=structure_id(ns, i_xml), package="com.app", activity=".MainActivity",
        profile_name="compose",
        value_bearing=outside_region_value_bearing(i_xml, COMPOSE_PROFILE),
    )
    target = Screen(
        id=fingerprint(ns, t_xml, COMPOSE_PROFILE), namespace=ns,
        structure_id=structure_id(ns, t_xml), package="com.app", activity=".MainActivity",
        profile_name="compose",
        value_bearing=outside_region_value_bearing(t_xml, COMPOSE_PROFILE),
    )
    assert intermediate.structure_id == target.structure_id and intermediate.id != target.id
    splash = Screen(
        id="splash", namespace="com.app/.SplashActivity", package="com.app",
        activity=".SplashActivity",
        force_action=ForceAction("am_start", "com.app/.SplashActivity", verified_fp="splash"),
    )
    g = Graph()
    g.upsert_screen(splash)
    g.upsert_screen(intermediate)
    g.upsert_screen(target)
    g.add_transition(Transition(
        source=intermediate.id, target=target.id,
        action=Action(selector=Selector("resource_id", "com.app:id/item"), action_type="click"),
    ))
    drv = FakeDriver(
        hierarchies=[i_live] * 3 + [t_xml] * 3,  # on I (dynamic), then the tap lands on T
        dumpsys_pairs=[_dumpsys(ns)] * 6,
        present_selectors={("resource_id", "com.app:id/item")},
        display=(1080, 2340),
    )
    out = navigate(g, splash.id, target.id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"  # routed I->T, then EXACT-confirmed T
    assert ("resource_id", "com.app:id/item", "click") in drv.taps  # it actually walked


def test_dynamic_anchor_trusted_by_namespace_routes_without_relaunch():
    # Instagram-class home: am_start lands us on the app, but its feed skeleton differs
    # from record so neither EXACT nor structure_id matches. The navigator must TRUST the
    # forced anchor (by namespace) and route from it — NOT relaunch the app repeatedly.
    ns_home = "com.app/.MainTabActivity"
    ns_modal = "com.app/.ModalActivity"
    home_rec = _vscreen("com.app", ".MainTabActivity", rid="feed_v1")
    home_live = _vscreen("com.app", ".MainTabActivity", rid="feed_v2_totally_different")  # skeleton changed
    modal = _vscreen("com.app", ".ModalActivity", rid="modal")
    home = Screen(
        id=fingerprint(ns_home, home_rec), namespace=ns_home,
        structure_id=structure_id(ns_home, home_rec), package="com.app", activity=".MainTabActivity",
        force_action=ForceAction("am_start", "com.app/.MainTabActivity",
                                  verified_fp=fingerprint(ns_home, home_rec)),
    )
    mod = Screen(id=fingerprint(ns_modal, modal), namespace=ns_modal,
                 structure_id=structure_id(ns_modal, modal), package="com.app", activity=".ModalActivity")
    g = Graph()
    g.upsert_screen(home)
    g.upsert_screen(mod)
    g.add_transition(Transition(source=home.id, target=mod.id,
                                action=Action(selector=Selector("text", "Go"), action_type="click")))
    drv = FakeDriver(
        # the CHANGED home (unrecognized -> ONE gated recovery launch whose gate re-observes
        # the home and verifies the namespace), then the modal after the routed tap
        hierarchies=[home_live] * 3 + [home_live] * 3 + [modal] * 3,
        dumpsys_pairs=[_dumpsys(ns_home)] * 6 + [_dumpsys(ns_modal)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    out = navigate(g, home.id, mod.id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"  # routed from the (gate-verified) trusted anchor
    # exactly ONE gated launch — the trust that lets the loop route from the unrecognizable
    # dynamic home is EARNED by the verify_foreground gate, never assumed from first contact
    assert drv.app_starts == [("com.app", ".MainTabActivity", True)]


def test_distinct_swipes_same_element_both_recorded():
    # review-2 #B: two swipes from the same element that differ only in direction (end)
    # must both survive dedup.
    S, Gs = ("com.app", ".SActivity"), ("com.app", ".GActivity")
    seq = [S, Gs, S, Gs]
    hs, ds = [], []
    for pkg, act in seq:
        hs += [_vscreen(pkg, act)] * 3
        ds += [_dumpsys(f"{pkg}/{act}")] * 3
    drv = FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    up = Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=560, x2=540, y2=100)
    tap = Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560)
    down = Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=560, x2=540, y2=2200)
    s.record_gesture(up)    # S -> G (swipe up)
    s.record_gesture(tap)   # G -> S
    s.record_gesture(down)  # S -> G (swipe down) — distinct end, must NOT dedup
    s_id = next(n for n in s.graph.g.nodes if s.graph.screen(n).namespace == "com.app/.SActivity")
    g_id = next(n for n in s.graph.g.nodes if s.graph.screen(n).namespace == "com.app/.GActivity")
    assert s.graph.g.number_of_edges(s_id, g_id) == 2  # up and down both kept


# ---------- #3: adapter_list_dominant must ignore the IME overlay ----------

def _list_with_keyboard(items):
    rows = "".join(
        f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/row" '
        f'clickable="true" content-desc="" text="item {i}" bounds="[0,{i*80}][1080,{i*80+80}]"/>'
        for i in range(items)
    )
    keys = "".join(
        f'<node class="android.inputmethodservice.Keyboard$Key" package="com.google.android.inputmethod.latin" '
        f'resource-id="" clickable="true" content-desc="" text="{c}" bounds="[{i*40},2000][{i*40+40},2080]"/>'
        for i, c in enumerate("qwertyuiopasdfghjklzxcvbnm")
    )
    return (
        '<hierarchy>'
        '<node class="androidx.recyclerview.widget.RecyclerView" package="com.app" '
        f'resource-id="com.app:id/list" clickable="false" content-desc="" text="" bounds="[0,0][1080,1990]">{rows}</node>'
        '<node class="android.inputmethodservice.SoftInputWindow" package="com.google.android.inputmethod.latin" '
        f'resource-id="" clickable="false" content-desc="" text="" bounds="[0,1990][1080,2340]">{keys}</node>'
        '</hierarchy>'
    )


def test_adapter_list_dominant_excludes_ime_overlay():
    xml = _list_with_keyboard(4)
    # without overlay-awareness the 26 key leaves drown the 4 rows below threshold
    assert adapter_list_dominant(xml, focus_pkg="com.app") is True


def test_verify_unverifiable_on_list_with_keyboard_open():
    ns = "com.app/.SearchActivity"
    recorded, observed = _list_with_keyboard(3), _list_with_keyboard(9)
    vid = "V" + hashlib.sha1(ns.encode()).hexdigest()[:15]
    s = Screen(id=vid, namespace=ns, structure_id=structure_id(ns, recorded, focus_pkg="com.app"),
               package="com.app", profile_name="volatile")
    assert verify_match(observed, ns, s, FakeDriver(), focus_pkg="com.app") == Tier.UNVERIFIABLE


# ---------- #5: revisiting a screen must not duplicate actions / parallel edges ----------

def _vscreen(pkg, act, rid="ok"):
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/{rid}" '
        f'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/></node></hierarchy>'
    )


def test_revisit_does_not_duplicate_action_or_parallel_edge():
    A, B = ("com.app", ".AActivity"), ("com.app", ".BActivity")
    seq = [A, B, A, B]  # A->B, B->A, A->B (the last repeats the first edge)
    hs, ds = [], []
    for pkg, act in seq:
        hs += [_vscreen(pkg, act)] * 3
        ds += [_dumpsys(f"{pkg}/{act}")] * 3
    drv = FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    tap = Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560)
    s.record_gesture(tap)  # A->B
    s.record_gesture(tap)  # B->A
    s.record_gesture(tap)  # A->B again (same selector) -> must dedup
    a_id = next(n for n in s.graph.g.nodes if s.graph.screen(n).namespace == "com.app/.AActivity")
    b_id = next(n for n in s.graph.g.nodes if s.graph.screen(n).namespace == "com.app/.BActivity")
    assert len(s.graph.screen(a_id).actions) == 1  # not 2
    assert s.graph.g.number_of_edges(a_id, b_id) == 1  # not 2 parallel duplicates


# ---------- #6: merge_screens must union intra_actions ----------

def test_merge_screens_unions_intra_actions():
    g = Graph()
    g.upsert_screen(Screen(id="keep", namespace="com.app/.A"))
    dup = Screen(
        id="dup", namespace="com.app/.A",
        intra_actions=[Action(selector=Selector("resource_id", "com.app:id/scroll"),
                              action_type="swipe", intent="reveal")],
    )
    g.upsert_screen(dup)
    g.merge_screens("keep", "dup")
    keep = g.screen("keep")
    assert "dup" not in g.g.nodes
    assert len(keep.intra_actions) == 1 and keep.intra_actions[0].intent == "reveal"
