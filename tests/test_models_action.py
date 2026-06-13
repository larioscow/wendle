from wendle.models import Action, Selector, Transition


def test_sensitive_action_repr_redacts_value_and_selector():
    action = Action(
        selector=Selector("resource_id", "com.app:id/password"),
        action_type="set_text",
        value={"param": "password"},
        sensitive=True,
    )
    text = repr(action)
    assert "<redacted>" in text
    assert "com.app:id/password" not in text
    assert "param" not in text


def test_nonsensitive_action_repr_is_readable():
    action = Action(selector=Selector("text", "Log in"), action_type="click")
    assert "Log in" in repr(action)


def test_transition_holds_action_and_confirmation_flag():
    action = Action(selector=Selector("text", "Pay"), action_type="click")
    t = Transition(source="a", target="b", action=action, needs_confirmation=True)
    assert t.weight == 1.0
    assert t.needs_confirmation is True
    assert t.action.selector.value == "Pay"
