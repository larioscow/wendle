"""B2 — the recorded graph must expose its transitions in CHRONOLOGICAL order, because
nx.MultiDiGraph.edges() groups by source node (so a revisited screen reorders the trace).
ordered_transitions() is the faithful linear trace the replay engine re-enacts.
"""
from wendle.graph import Graph
from wendle.models import Action, Screen, Selector, Transition


def _t(src, tgt, val):
    return Transition(source=src, target=tgt,
                      action=Action(selector=Selector("text", val), action_type="click"))


def _build():
    g = Graph()
    for sid in ("A", "B", "C"):
        g.upsert_screen(Screen(id=sid, namespace=f"app/.{sid}"))
    g.add_transition(_t("A", "B", "go1"))
    g.add_transition(_t("B", "A", "back"))  # revisit A — this is what breaks edges() order
    g.add_transition(_t("A", "C", "go2"))
    return g


def _order(g):
    return [(u, v, d["action"].selector.value) for (u, v, _k, d) in g.ordered_transitions()]


def test_ordered_transitions_is_chronological_through_revisits():
    g = _build()
    assert _order(g) == [("A", "B", "go1"), ("B", "A", "back"), ("A", "C", "go2")]
    # nx.edges() groups A's two out-edges together -> NOT the order they happened
    nx_pairs = [(u, v) for (u, v, _k) in g.g.edges(keys=True)]
    assert nx_pairs == [("A", "B"), ("A", "C"), ("B", "A")]  # grouped-by-source, not chronological


def test_ordered_transitions_survives_round_trip():
    g2 = Graph.from_json(_build().to_json())
    assert _order(g2) == [("A", "B", "go1"), ("B", "A", "back"), ("A", "C", "go2")]
