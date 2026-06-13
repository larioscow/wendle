from wendle.graph import Graph
from wendle.models import Action, Screen, Selector, Transition


def _g():
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace="com.sec.android.app.launcher/.Home",
                           screen_type="homescreen", package="com.sec.android.app.launcher"))
    g.upsert_screen(Screen(id="a", namespace="com.app/.A", package="com.app", activity=".A"))
    g.upsert_screen(Screen(id="b", namespace="com.app/.B", package="com.app", activity=".B"))
    g.upsert_screen(Screen(id="x", namespace="com.other/.X", package="com.other", activity=".X"))
    return g


def _edge(src, tgt, **kw):
    return Transition(src, tgt, Action(selector=Selector("text", "Go"), action_type="click"), **kw)


def test_self_loops_excluded():
    g = _g()
    g.add_transition(_edge("a", "a"))
    g.add_transition(_edge("a", "b"))
    sub = g.routable_subgraph()
    assert not sub.has_edge("a", "a")
    assert sub.has_edge("a", "b")


def test_launcher_incident_edges_excluded():
    g = _g()
    g.add_transition(_edge("L", "a"))  # home -> app icon tap
    g.add_transition(_edge("b", "L"))  # app -> home (back/home)
    sub = g.routable_subgraph()
    assert not sub.has_edge("L", "a")
    assert not sub.has_edge("b", "L")
    assert "L" in sub.nodes  # node preserved; only its edges are non-routable


def test_same_package_edge_not_cross_app():
    g = _g()
    g.add_transition(_edge("a", "b"))
    sub = g.routable_subgraph()
    _, _, data = next(iter(sub.edges(data=True)))
    assert data["cross_app"] is False


def test_cross_package_edge_kept_and_typed():
    g = _g()
    g.add_transition(_edge("a", "x"))  # com.app -> com.other (share/OAuth hop)
    sub = g.routable_subgraph()
    assert sub.has_edge("a", "x")
    _, _, data = next(iter(sub.edges(data=True)))
    assert data["cross_app"] is True


def test_edge_payload_preserved():
    g = _g()
    g.add_transition(_edge("a", "b", action_class="swipe", weight=1.5))
    sub = g.routable_subgraph()
    _, _, data = next(iter(sub.edges(data=True)))
    assert data["action_class"] == "swipe" and data["weight"] == 1.5
    assert data["action"].selector.value == "Go"
