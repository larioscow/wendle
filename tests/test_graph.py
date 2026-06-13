from wendle.graph import Graph
from wendle.models import (
    Action,
    DeviceProfile,
    ForceAction,
    Screen,
    Selector,
    Transition,
)


def _graph_with_sensitive():
    g = Graph()
    g.device_profile = DeviceProfile(
        touchscreen_node="/dev/input/event3",
        abs_x=(0, 4095),
        abs_y=(0, 4095),
        display=(1080, 2400),
        touch_protocol="type_b",
    )
    home = Screen(
        id="Labc",
        namespace="com.sec.android.app.launcher/.activities.LauncherActivity",
        screen_type="homescreen",
        force_action=ForceAction("keyevent", "3", verified_fp="Labc"),
    )
    login = Screen(
        id="f00d",
        namespace="com.app/.LoginActivity",
        actions=[
            Action(selector=Selector("text", "Sign in"), action_type="click"),
            Action(
                selector=Selector("resource_id", "com.app:id/password"),
                action_type="set_text",
                value={"param": "password"},
                sensitive=True,
            ),
            Action(selector=Selector("coords", (540, 1200)), action_type="click",
                   replayability="coordinate_only", end=(540, 1800)),
        ],
    )
    g.upsert_screen(home)
    g.upsert_screen(login)
    g._profiles[home.namespace] = "launcher"
    g._profiles[login.namespace] = "view"
    g.add_transition(
        Transition(source="Labc", target="f00d",
                   action=Action(selector=Selector("text", "App"), action_type="click"),
                   settled=True, landed_on_real_element=True)
    )
    return g


def test_round_trip_equality():
    g = _graph_with_sensitive()
    restored = Graph.from_json(g.to_json())
    assert set(restored.g.nodes) == set(g.g.nodes)
    assert restored.g.number_of_edges() == g.g.number_of_edges()
    assert restored.device_profile == g.device_profile
    assert restored._profiles == g._profiles
    s = restored.screen("f00d")
    # coords selector re-tupled, end re-tupled, sensitive value preserved
    coord_action = next(a for a in s.actions if a.selector.kind == "coords")
    assert coord_action.selector.value == (540, 1200)
    assert coord_action.end == (540, 1800)
    fa = restored.screen("Labc").force_action
    assert fa.kind == "keyevent" and fa.verified


def test_no_secret_literal_and_no_hooks_in_json():
    g = _graph_with_sensitive()
    g.hooks["f00d"] = [lambda d: None]  # attach a hook — must NOT serialize
    blob = g.to_json()
    assert "hunter2" not in blob  # no secret literal (none was ever stored)
    assert "hooks" not in blob  # the hooks registry is never serialized
    assert "lambda" not in blob and "function" not in blob


def test_sensitive_action_stores_only_param_handle():
    g = _graph_with_sensitive()
    blob = g.to_json()
    restored = Graph.from_json(blob)
    pw = next(
        a
        for a in restored.screen("f00d").actions
        if a.selector.kind == "resource_id"
    )
    assert pw.sensitive is True
    assert pw.value == {"param": "password"}


def test_anchors_lists_verified_force_actions():
    g = _graph_with_sensitive()
    assert g.anchors() == ["Labc"]


def _click(label):
    return Action(selector=Selector("text", label), action_type="click")


def test_merge_screens_keeps_the_ordered_trace_replayable():
    # merge_screens must keep _edge_order (the faithful replay trace) consistent: redirected
    # edges stay at their chronological slots, and the trace/save survive the merge. It used
    # to leave stale (..,dup,..) entries -> ordered_transitions()/to_json()/save() KeyError'd
    # on the removed node, crashing the end-of-session save and losing the recording.
    g = Graph()
    for sid in ("S0", "DUP", "S2", "KEEP"):
        g.upsert_screen(Screen(id=sid, namespace=f"com.app/.{sid}"))
    g.add_transition(Transition(source="S0", target="DUP", action=_click("a")))
    g.add_transition(Transition(source="DUP", target="S2", action=_click("b")))
    g.merge_screens("KEEP", "DUP")
    order = [(u, v) for (u, v, _k, _d) in g.ordered_transitions()]
    assert order == [("S0", "KEEP"), ("KEEP", "S2")]  # chronological, redirected, none dropped
    assert "DUP" not in g.g.nodes
    restored = Graph.from_json(g.to_json())  # the save path survives the merge
    assert restored.g.number_of_edges() == 2


def test_merge_screens_self_loop_redirects_once():
    g = Graph()
    g.upsert_screen(Screen(id="KEEP", namespace="com.app/.K"))
    g.upsert_screen(Screen(id="DUP", namespace="com.app/.D"))
    g.add_transition(Transition(source="DUP", target="DUP", action=_click("x")))
    g.merge_screens("KEEP", "DUP")
    assert g.g.number_of_edges() == 1  # ONE keep->keep loop, not double-added
    assert [(u, v) for (u, v, _k, _d) in g.ordered_transitions()] == [("KEEP", "KEEP")]


def test_upsert_revisit_unions_actions():
    g = Graph()
    s1 = Screen(id="x", namespace="com.app/.A",
                actions=[Action(selector=Selector("text", "A"), action_type="click")])
    assert g.upsert_screen(s1) is True
    s2 = Screen(id="x", namespace="com.app/.A",
                actions=[Action(selector=Selector("text", "B"), action_type="click")])
    assert g.upsert_screen(s2) is False  # revisit
    assert len(g.screen("x").actions) == 2  # unioned


def test_rekey_screen_rewrites_frozen_force_action_verified_fp():
    # task #17b ORDER B: a split rekeys an ANCHORED twin's node (F -> T_old). ForceAction is
    # frozen and verified_fp pins the old id (consumed as the flow start_id + launch cache key),
    # so rekey_screen must replace() it to the new id — at the rekey layer, covering every caller.
    g = Graph()
    g.upsert_screen(Screen(id="F", namespace="com.app/.A", structure_id="S1",
                           force_action=ForceAction("am_start", "com.app/.A", verified_fp="F")))
    old_fa = g.screen("F").force_action
    g.rekey_screen("F", "Tnew")
    assert g.screen("Tnew").force_action.verified_fp == "Tnew"  # the pinned id followed the rename
    assert old_fa.verified_fp == "F"  # the frozen original object is untouched (replace, not mutate)


def test_rekey_screen_leaves_unrelated_verified_fp_alone():
    # a node whose anchor pins a DIFFERENT id (a foreign-app anchor borrowed onto it) is not rewritten.
    g = Graph()
    g.upsert_screen(Screen(id="F", namespace="com.app/.A",
                           force_action=ForceAction("am_start", "com.app/.A", verified_fp="other")))
    g.rekey_screen("F", "Tnew")
    assert g.screen("Tnew").force_action.verified_fp == "other"  # only verified_fp==old_id is rewritten
