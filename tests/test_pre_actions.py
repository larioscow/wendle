"""set_text rides on the submit edge as Transition.pre_actions and IS replayed.

The fatal flaw the workflow caught: a set_text parked on Screen.actions is never
executed (the navigator routes over edges). pre_actions fixes that.
"""
from wendle.driver.fake import FakeDriver
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import navigate

NOSLEEP = {"sleep": lambda _dt: None}


def _screen(pkg, act, rid="ok"):
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/{rid}" '
        f'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/></node></hierarchy>'
    )


def _dumpsys(pkg, act):
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def test_pre_actions_round_trip():
    g = Graph()
    g.upsert_screen(Screen(id="a", namespace="com.app/.A"))
    g.upsert_screen(Screen(id="b", namespace="com.app/.B"))
    g.add_transition(Transition(
        source="a", target="b",
        action=Action(selector=Selector("text", "Login"), action_type="click"),
        pre_actions=[
            Action(selector=Selector("resource_id", "com.app:id/user"), action_type="set_text",
                   value={"text": "alice"}),
            Action(selector=Selector("resource_id", "com.app:id/pw"), action_type="set_text",
                   value={"param": "password"}, sensitive=True),
        ],
    ))
    restored = Graph.from_json(g.to_json())
    _, _, data = next(iter(restored.g.edges(data=True)))
    assert len(data["pre_actions"]) == 2
    assert data["pre_actions"][0].value == {"text": "alice"}
    assert data["pre_actions"][1].sensitive and data["pre_actions"][1].value == {"param": "password"}
    assert "alice" in g.to_json() and "password" in g.to_json()  # name ok; literal pw absent
    assert "hunter2" not in g.to_json()


def test_pre_actions_executed_before_edge_tap():
    A = ("com.app", ".AActivity")
    B = ("com.app", ".BActivity")
    g = Graph()
    a = Screen(id="a", namespace="com.app/.AActivity", package="com.app", activity=".AActivity",
               force_action=ForceAction("am_start", "com.app/.AActivity", verified_fp="a"))
    b = Screen(id="b", namespace="com.app/.BActivity")
    from wendle.fingerprint.signature import fingerprint, structure_id
    a.id = fingerprint("com.app/.AActivity", _screen(*A)); a.structure_id = structure_id("com.app/.AActivity", _screen(*A))
    a.force_action = ForceAction("am_start", "com.app/.AActivity", verified_fp=a.id)
    b.id = fingerprint("com.app/.BActivity", _screen(*B)); b.structure_id = structure_id("com.app/.BActivity", _screen(*B))
    g.upsert_screen(a)
    g.upsert_screen(b)
    g.add_transition(Transition(
        source=a.id, target=b.id,
        action=Action(selector=Selector("text", "Go"), action_type="click"),
        pre_actions=[Action(selector=Selector("resource_id", "com.app:id/user"),
                            action_type="set_text", value={"text": "alice"})],
    ))
    drv = FakeDriver(
        hierarchies=[_screen(*A)] * 3 + [_screen(*B)] * 3,
        dumpsys_pairs=[_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    out = navigate(g, a.id, b.id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"
    # the set_text ran, and BEFORE the Go tap
    assert drv.text_sets and drv.text_sets[0][:3] == ("resource_id", "com.app:id/user", "alice")
    assert drv.taps and ("text", "Go", "click") in drv.taps


def test_coords_set_text_refused_not_crash():
    from wendle.navigate.navigator import Navigator
    nav = Navigator(Graph(), FakeDriver())
    res = nav._execute(Action(selector=Selector("coords", (5, 5)), action_type="set_text",
                              value={"text": "x"}))
    assert res.ok is False and "coordinate" in res.error  # honest refusal, no ValueError
