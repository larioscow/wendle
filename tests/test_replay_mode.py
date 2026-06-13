"""Replay-mode resolution: atomic default, recorded per_key, developer override."""
from wendle.driver.fake import FakeDriver
from wendle.graph import Graph
from wendle.models import Action, Selector
from wendle.navigate.navigator import Navigator


def _exec(action, replay_modes=None):
    nav = Navigator(Graph(), FakeDriver(), replay_modes=replay_modes)
    res = nav._execute(action)
    return nav.driver.text_sets, res.ok, res.error


def _set_text(value, sel=("resource_id", "com.app:id/q")):
    return Action(selector=Selector(*sel), action_type="set_text", value=value)


def test_atomic_default_uses_set_text():
    sets, ok, _ = _exec(_set_text({"text": "hi"}))
    assert ok and sets == [("resource_id", "com.app:id/q", "hi")]  # 3-tuple = atomic set_text


def test_recorded_per_key_routes_to_type_text():
    sets, ok, _ = _exec(_set_text({"text": "hi", "replay_mode": "per_key"}))
    assert ok and sets[0] == ("resource_id", "com.app:id/q", "hi", "per_key")  # 4-tuple = type_text


def test_developer_override_forces_per_key_by_selector():
    sets, ok, _ = _exec(_set_text({"text": "hi"}),
                        replay_modes={"com.app:id/q": "per_key"})
    assert sets[0][-1] == "per_key"  # override beats the recorded 'atomic' default


def test_developer_override_can_force_atomic_over_recorded_per_key():
    sets, ok, _ = _exec(_set_text({"text": "hi", "replay_mode": "per_key"}),
                        replay_modes={"com.app:id/q": "atomic"})
    assert sets == [("resource_id", "com.app:id/q", "hi")]  # forced atomic


def test_override_by_param_name():
    a = Action(selector=Selector("resource_id", "com.app:id/pw"), action_type="set_text",
               value={"param": "password"}, sensitive=True)
    nav = Navigator(Graph(), FakeDriver(present_selectors=set()),
                    params={"password": "hunter2"}, replay_modes={"password": "per_key"})
    nav._execute(a)
    assert nav.driver.text_sets[0] == ("resource_id", "com.app:id/pw", "hunter2", "per_key")
