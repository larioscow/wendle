from wendle.graph import Graph
from wendle.models import Action, Screen, Selector, Transition


def test_screen_structure_id_round_trips():
    g = Graph()
    g.upsert_screen(Screen(id="a", namespace="com.app/.A", structure_id="Sdead"))
    restored = Graph.from_json(g.to_json())
    assert restored.screen("a").structure_id == "Sdead"


def test_screen_structure_id_defaults_none_on_legacy_json():
    # a graph dict without structure_id (pre-Spike3) loads with structure_id=None
    g = Graph()
    g.upsert_screen(Screen(id="a", namespace="com.app/.A"))
    restored = Graph.from_json(g.to_json())
    assert restored.screen("a").structure_id is None


def test_action_intent_round_trips():
    g = Graph()
    s = Screen(
        id="a",
        namespace="com.app/.A",
        actions=[
            Action(selector=Selector("text", "Go"), action_type="click", intent="navigate"),
            Action(selector=Selector("text", "More"), action_type="swipe", intent="reveal"),
        ],
    )
    g.upsert_screen(s)
    restored = Graph.from_json(g.to_json())
    intents = {a.selector.value: a.intent for a in restored.screen("a").actions}
    assert intents == {"Go": "navigate", "More": "reveal"}


def test_transition_action_class_round_trips():
    g = Graph()
    g.upsert_screen(Screen(id="a", namespace="com.app/.A"))
    g.upsert_screen(Screen(id="b", namespace="com.app/.B"))
    g.add_transition(
        Transition(
            source="a",
            target="b",
            action=Action(selector=Selector("text", "Go"), action_type="swipe"),
            action_class="swipe",
        )
    )
    restored = Graph.from_json(g.to_json())
    _, _, data = next(iter(restored.g.edges(data=True)))
    assert data["action_class"] == "swipe"


def test_action_class_defaults_navigate():
    t = Transition(source="a", target="b",
                   action=Action(selector=Selector("text", "Go"), action_type="click"))
    assert t.action_class == "navigate"
