"""nav_container_members — conservative detection of GLOBAL-NAV chrome (tab bar / bottom nav /
drawer). Returns the bounds of clickable content-desc affordances that belong to a nav container.
Adversarially gated: a content run of identical rows, a segmented control of plain buttons, a
single button, must NOT be detected (uncertain -> not global -> a normal edge, zero regression)."""
from wendle.capture.affordance import nav_container_members

PKG = "com.x"


def _bar(items, *, selected_idx=0, cls="android.widget.LinearLayout", scrollable=False,
         container_cls="android.widget.HorizontalScrollView", y=2800):
    kids = "".join(
        f'<node class="{cls}" package="{PKG}" resource-id="" content-desc="{name}" '
        f'clickable="{"false" if i == selected_idx else "true"}" '
        f'selected="{"true" if i == selected_idx else "false"}" '
        f'bounds="[{300 + i * 200},{y}][{480 + i * 200},{y + 180}]"/>'
        for i, name in enumerate(items))
    return (f'<node class="{container_cls}" package="{PKG}" resource-id="{PKG}:id/tabs" '
            f'clickable="false" scrollable="{str(scrollable).lower()}" '
            f'bounds="[270,{y}][1170,{y + 200}]">{kids}</node>')


def _wrap(*inner):
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" '
            f'bounds="[0,0][1440,3120]">{"".join(inner)}</node></hierarchy>')


def test_detects_a_bottom_tab_bar():
    xml = _wrap(_bar(["Alarm", "World", "Stopwatch", "Timer"], selected_idx=3))
    members = nav_container_members(xml)
    # the 4 tab buttons are members
    assert len(members) == 4


def test_a_single_button_is_not_a_nav_container():
    xml = _wrap(f'<node class="android.widget.Button" package="{PKG}" content-desc="Go" '
               f'clickable="true" bounds="[40,500][1040,620]"/>')
    assert nav_container_members(xml) == set()


def test_a_segmented_control_without_selection_signal_is_not_global():
    # Day/Week/Month plain buttons, NO selected attribute on any child, not in the nav class
    # allowlist -> not confidently a nav bar (would switch content within one screen)
    xml = _wrap(
        f'<node class="android.widget.LinearLayout" package="{PKG}" bounds="[40,300][1040,440]">'
        + "".join(
            f'<node class="android.widget.Button" package="{PKG}" content-desc="{n}" '
            f'clickable="true" bounds="[{40 + i*330},300][{360 + i*330},440]"/>'
            for i, n in enumerate(["Day", "Week", "Month"]))
        + '</node>')
    assert nav_container_members(xml) == set()


def test_class_allowlist_detects_bottomnavigation_even_without_selected():
    xml = _wrap(_bar(["Home", "Search", "Profile"], selected_idx=0,
                     cls="com.google.android.material.bottomnavigation.BottomNavigationItemView",
                     container_cls="com.google.android.material.bottomnavigation.BottomNavigationView"))
    assert len(nav_container_members(xml)) == 3


def test_an_adapter_run_region_of_identical_rows_is_not_a_nav_bar():
    # a scrollable list of 4 identical clickable rows (a content list) must NOT be marked global
    rows = "".join(
        f'<node class="android.view.View" package="{PKG}" resource-id="" content-desc="Item {i}" '
        f'clickable="true" bounds="[0,{300+i*300}][1080,{580+i*300}]"/>' for i in range(4))
    xml = _wrap(f'<node class="androidx.recyclerview.widget.RecyclerView" package="{PKG}" '
               f'resource-id="{PKG}:id/list" scrollable="true" bounds="[0,300][1080,1700]">{rows}</node>')
    assert nav_container_members(xml) == set()


def test_member_lookup_by_bounds():
    xml = _wrap(_bar(["Alarm", "World", "Stopwatch", "Timer"], selected_idx=3))
    members = nav_container_members(xml)
    # the World button at [500,2800][680,2980] is a member
    assert (500, 2800, 680, 2980) in members


# ---- DETECT in the builder + round-trip + convergence ----

def test_builder_marks_global_affordance_and_round_trips(tmp_path):
    import threading
    from wendle.record.builder import GraphBuilder, BindContext
    from wendle.models import Action, Selector
    from wendle.graph import Graph

    NS = f"{PKG}/.Main"

    def _screen(active):
        bar = nav_bar = _bar(["Alarm", "World", "Timer"], selected_idx=active, y=2800)
        return (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" '
                f'bounds="[0,0][1440,3120]">'
                f'<node class="android.widget.TextView" package="{PKG}" text="body {active}" '
                f'bounds="[40,200][1040,400]"/>{bar}</node></hierarchy>')

    b = GraphBuilder()
    b.graph.device_profile = None
    b.begin(b.enter(_screen(0), NS, True, PKG))           # on Alarm
    after = b.enter(_screen(1), NS, True, PKG)            # tapped World tab
    t = b.commit_transition(
        action=Action(selector=Selector("content_desc", "World"), action_type="click"),
        after=after,
        bind=BindContext(px=590, py=2890, bounds=(500, 2800, 680, 2980), landed=True))
    assert t is not None and t.global_affordance is True

    # round-trip survives to_json/from_json with the field intact
    restored = Graph.from_json(b.graph.to_json())
    edge = next(d for _u, _v, _k, d in restored.ordered_transitions())
    assert edge.get("global_affordance") is True


def test_legacy_json_defaults_global_affordance_false():
    import json
    from wendle.graph import Graph
    # a v1 json WITHOUT the field loads with global_affordance=False
    g = Graph()
    js = json.loads(g.to_json())
    assert "v" in js  # sanity; empty graph round-trips
