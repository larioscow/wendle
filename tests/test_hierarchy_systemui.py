"""RULE 1 — a tap/selector must never bind to system UI. parse_hierarchy drops
com.android.systemui (status/nav-bar chrome — the OS clock) when the foreground is the app,
so node_at can't return it and a selector can never be synthesized from it.
"""
from wendle.capture.hierarchy import node_at, parse_hierarchy

APP = "mx.com.miapp"


def _xml():
    return ('<hierarchy>'
            '<node class="android.widget.FrameLayout" package="mx.com.miapp" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,100][1080,2000]">'
            '<node class="android.widget.Button" package="mx.com.miapp" resource-id="mx.com.miapp:id/go" '
            'clickable="true" content-desc="" text="Ingresar" bounds="[40,500][1040,620]"/></node>'
            '<node class="android.widget.TextView" package="com.android.systemui" '
            'resource-id="com.android.systemui:id/clock" clickable="true" content-desc="" '
            'text="7:11" bounds="[20,0][120,80]"/></hierarchy>')


def test_systemui_clock_stripped_when_focus_is_app():
    nodes = parse_hierarchy(_xml(), focus_pkg=APP)
    assert all(n.package != "com.android.systemui" for n in nodes)
    assert all(n.text != "7:11" for n in nodes)  # the clock can never become a selector
    hit = node_at(nodes, 60, 40)  # a tap on the status-bar clock pixel
    assert hit is None or hit.package == APP  # binds to nothing / the app — never systemui


def test_systemui_kept_when_focus_is_systemui():
    # deliberately recording the shade itself: the focus gate (mirrors _should_prune) keeps it
    nodes = parse_hierarchy(_xml(), focus_pkg="com.android.systemui")
    assert any(n.text == "7:11" for n in nodes)


def test_focus_none_keeps_all_nodes_backward_compatible():
    nodes = parse_hierarchy(_xml())  # focus unknown -> strip nothing (all existing tests unaffected)
    assert any(n.text == "7:11" for n in nodes)
    assert any(n.package == APP for n in nodes)


def test_app_node_carries_package():
    nodes = parse_hierarchy(_xml(), focus_pkg=APP)
    go = next(n for n in nodes if n.text == "Ingresar")
    assert go.package == "mx.com.miapp"
