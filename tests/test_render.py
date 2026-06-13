"""Offline static map render (the design kept it for v1): emit a Graphviz DOT of the recorded map —
dependency-light (text, no binary) and REDACTION-SAFE (never a selector value / typed secret)."""
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.render import render, to_dot


def _graph():
    g = Graph()
    g.upsert_screen(Screen(id="A", namespace="com.app/.Home", screen_type="app", package="com.app",
                           activity=".Home", force_action=ForceAction("am_start", "com.app/.Home", verified_fp="A")))
    g.upsert_screen(Screen(id="Tnet", namespace="com.app/.Sub", structure_id="S1", coarse_id="F",
                           chrome_digest="d1", adapter_dominant=True))  # a refined twin
    g.add_transition(Transition(source="A", target="Tnet",
                                action=Action(selector=Selector("text", "SECRET_LABEL"), action_type="click")))
    return g


def test_to_dot_emits_nodes_edges_redaction_safe():
    dot = to_dot(_graph())
    assert dot.startswith("digraph") and dot.rstrip().endswith("}")
    assert '"A"' in dot and '"Tnet"' in dot                  # both screens are nodes
    assert "com.app/.Home" in dot and "com.app/.Sub" in dot  # namespaces labeled
    assert "click" in dot and "text" in dot                  # edge labeled by action:selector KIND
    assert "SECRET_LABEL" not in dot                          # NEVER the selector VALUE (redaction)
    assert "anchor" in dot.lower() or "am_start" in dot.lower()  # the launch anchor is marked
    assert "twin" in dot.lower() or "coarse" in dot.lower()      # the refined twin is marked


def test_render_writes_a_dot_file(tmp_path):
    out = str(tmp_path / "map.dot")
    path = render(_graph(), out)
    assert path == out
    body = open(out).read()
    assert body.startswith("digraph") and "Tnet" in body
