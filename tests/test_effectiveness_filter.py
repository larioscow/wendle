from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.graph import Graph
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
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


def _driver(*screens):
    hs, ds = [], []
    for pkg, act in screens:
        hs += [_screen(pkg, act)] * 3
        ds += [_dumpsys(pkg, act)] * 3
    return FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340))


def _tap():
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560)


def _swipe():
    return Gesture(kind="swipe", t_down=1.0, t_up=1.3, x=540, y=1800, x2=540, y2=600)


def test_settled_no_op_tap_records_no_edge_but_a_probe_intra_action():
    drv = _driver(("com.app", ".AActivity"), ("com.app", ".AActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    t = s.record_gesture(_tap())  # tap stays on the same settled screen -> no-op
    assert t is None
    assert s.graph.g.number_of_edges() == 0
    a = s.graph.screen(s.current_id)
    assert len(a.intra_actions) == 1 and a.intra_actions[0].intent == "probe"
    assert a.actions == []  # no-op never pollutes the navigate-action list


def test_settled_no_op_swipe_without_a_region_is_probe():
    # §2.7 (lazy-region design): `reveal` is reserved for REGION-BOUND content-advance
    # gestures — a swipe on a screen with no detected adapter region (here: a lone button)
    # is a pan/probe, stored but never replayable. Region-bound classification is covered
    # in test_recorder_lazy_rules.py.
    drv = _driver(("com.app", ".AActivity"), ("com.app", ".AActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    t = s.record_gesture(_swipe())
    assert t is None
    a = s.graph.screen(s.current_id)
    assert len(a.intra_actions) == 1 and a.intra_actions[0].intent == "probe"
    assert s.graph.g.number_of_edges() == 0


def test_real_edge_is_typed_and_navigate_intent():
    drv = _driver(("com.app", ".AActivity"), ("com.app", ".BActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    t = s.record_gesture(_tap())
    assert t is not None
    assert t.action_class == "navigate"
    assert t.action.intent == "navigate"
    _, _, data = next(iter(s.graph.g.edges(data=True)))
    assert data["action_class"] == "navigate"


def test_real_swipe_edge_typed_swipe_with_small_prior_not_old_penalty():
    drv = _driver(("com.app", ".AActivity"), ("com.app", ".BActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    t = s.record_gesture(_swipe())  # swipe that DID change screen -> real edge
    assert t is not None and t.action_class == "swipe"
    # old swipe_penalty was +1.5; the static prior is smaller
    assert t.weight < 1.0 + 1.5


def test_anchor_assigned_before_filter_on_home_to_app():
    drv = _driver(
        ("com.sec.android.app.launcher", ".activities.LauncherActivity"),
        ("com.app", ".MainActivity"),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap())
    app = s.graph.screen(s.current_id)
    assert app.force_action is not None and app.force_action.kind == "am_start"


def test_intra_actions_round_trip():
    drv = _driver(("com.app", ".AActivity"), ("com.app", ".AActivity"))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap())
    restored = Graph.from_json(s.graph.to_json())
    a = restored.screen(s.current_id)
    assert len(a.intra_actions) == 1 and a.intra_actions[0].intent == "probe"
