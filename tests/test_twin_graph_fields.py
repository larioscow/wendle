"""Task #17b-2: identity-refinement carriers on Screen/Graph — chrome_digest, coarse_id,
the _twin_exhausted blacklist, JSON round-trip with legacy defaults, and rekey_screen
(the F->T node rename that a split performs, preserving the faithful edge trace)."""
from wendle.graph import Graph
from wendle.models import Action, Screen, Selector, Transition


def _click(v):
    return Action(selector=Selector("text", v), action_type="click")


def test_screen_carries_chrome_digest_and_coarse_id_defaulting_none():
    s = Screen(id="x", namespace="com.app/.A")
    assert s.chrome_digest is None and s.coarse_id is None  # legacy/unrefined default
    s2 = Screen(id="T123", namespace="com.app/.A", chrome_digest="deadbeef", coarse_id="abc")
    assert s2.chrome_digest == "deadbeef" and s2.coarse_id == "abc"


def test_round_trip_preserves_refinement_fields_and_blacklist():
    g = Graph()
    g.upsert_screen(Screen(id="T1", namespace="com.app/.A", structure_id="S9",
                           chrome_digest="d1", coarse_id="F9"))
    g.upsert_screen(Screen(id="T2", namespace="com.app/.A", structure_id="S9",
                           chrome_digest="d2", coarse_id="F9"))
    g.add_transition(Transition(source="T1", target="T2", action=_click("go")))
    g.mark_twin_exhausted("Fdead")
    g2 = Graph.from_json(g.to_json())
    assert g2.screen("T1").chrome_digest == "d1" and g2.screen("T1").coarse_id == "F9"
    assert g2.screen("T2").chrome_digest == "d2"
    assert g2.is_twin_exhausted("Fdead") and not g2.is_twin_exhausted("Fother")


def test_legacy_graph_loads_with_none_defaults():
    # a graph saved before #17b: screens have no chrome_digest/coarse_id, no twin_exhausted key
    legacy = ('{"v":1,"device_profile":null,"fingerprint_config":{},'
              '"screens":[{"id":"abc","namespace":"com.app/.A","structure_id":"S1"}],'
              '"transitions":[]}')
    g = Graph.from_json(legacy)
    s = g.screen("abc")
    assert s.chrome_digest is None and s.coarse_id is None  # behaves exactly as today
    assert not g.is_twin_exhausted("anything")


def test_rekey_screen_renames_node_and_remaps_edges_and_trace():
    g = Graph()
    g.upsert_screen(Screen(id="F", namespace="com.app/.A", structure_id="S9"))
    g.upsert_screen(Screen(id="B", namespace="com.app/.B"))
    g.upsert_screen(Screen(id="C", namespace="com.app/.C"))
    g.add_transition(Transition(source="C", target="F", action=_click("in")))   # inbound to F
    g.add_transition(Transition(source="F", target="B", action=_click("out")))  # outbound from F
    g.rekey_screen("F", "Tnew")
    assert "F" not in g.g.nodes and "Tnew" in g.g.nodes
    assert g.screen("Tnew").id == "Tnew"                 # the Screen object's id moved too
    assert g.screen("Tnew").structure_id == "S9"         # other fields preserved
    assert g.g.has_edge("C", "Tnew") and g.g.has_edge("Tnew", "B")  # edges follow
    # the faithful chronological trace points at the new id, slots preserved
    order = [(u, v) for (u, v, _k, _d) in g.ordered_transitions()]
    assert ("C", "Tnew") in order and ("Tnew", "B") in order
    assert ("C", "F") not in order and ("F", "B") not in order
