"""Stateful non-navigating actions (checkbox/switch/radio) -> idempotent set_checked.

Grounded in Playwright setChecked / DroidBot SelectEvent: a value-carrying pre_action on
the submit edge, NOT a node/edge/node-state; the fingerprint stays blind to checked.
"""
from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.text_entry import detect_checkable_entry
from wendle.capture.types import Gesture, Snapshot, UINode
from wendle.driver.fake import FakeDriver
from wendle.graph import Graph
from wendle.models import Action, DeviceProfile, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
                        display=(1080, 2340), touch_protocol="type_b")
NOSLEEP = {"sleep": lambda _dt: None}


def _node(rid, checked, checkable=True):
    return UINode(cls="android.widget.CheckBox", resource_id=rid, text="", content_desc="",
                  clickable=True, password=False, bounds=(40, 200, 1040, 300),
                  checkable=checkable, checked=checked)


# ---- detector ----

def test_flip_false_to_true_emits_set_checked():
    a = detect_checkable_entry(_node("com.app:id/t", False), [_node("com.app:id/t", True)])
    assert a is not None and a.action_type == "set_checked" and a.value == {"checked": True}


def test_no_flip_returns_none():
    assert detect_checkable_entry(_node("r", True), [_node("r", True)]) is None


def test_non_checkable_returns_none():
    assert detect_checkable_entry(_node("r", False, checkable=False), [_node("r", True)]) is None


def test_no_resource_id_returns_none():
    assert detect_checkable_entry(_node("", False), [_node("", True)]) is None  # Compose-only -> probe


def test_radio_positive_select_only():
    # tapped option goes False->True; the auto-unchecked sibling is a different rid we never tapped
    a = detect_checkable_entry(_node("opt_b", False),
                               [_node("opt_a", False), _node("opt_b", True)])
    assert a.value == {"checked": True} and a.selector.value == "opt_b"


def test_selector_is_resource_id_not_state_label():
    # a stateful widget's label IS its state ('Off'->'On'); binding to it would break the
    # opposite-state replay. Must always select by resource_id.
    before = UINode(cls="android.widget.Switch", resource_id="com.app:id/wifi", text="",
                    content_desc="Wi-Fi, Off", clickable=True, password=False,
                    bounds=(0, 0, 100, 100), checkable=True, checked=False)
    after = UINode(cls="android.widget.Switch", resource_id="com.app:id/wifi", text="",
                   content_desc="Wi-Fi, On", clickable=True, password=False,
                   bounds=(0, 0, 100, 100), checkable=True, checked=True)
    a = detect_checkable_entry(before, [after])
    assert a.selector.kind == "resource_id" and a.selector.value == "com.app:id/wifi"
    assert "On" not in str(a.selector.value)  # the volatile label is NOT in the selector


def test_selected_flip_for_segmented_tab():
    # segmented/tab controls report the change in `selected`, not `checked`
    def tab(rid, sel):
        return UINode(cls="android.widget.TabWidget", resource_id=rid, text="", content_desc="",
                      clickable=True, password=False, bounds=(0, 0, 100, 100),
                      checkable=True, checked=False, selected=sel)
    a = detect_checkable_entry(tab("com.app:id/tabB", False), [tab("com.app:id/tabB", True)])
    assert a is not None and a.value == {"checked": True}  # selected-flip recorded as set_checked


# ---- parse ----

def test_parse_reads_checkable_checked_selected():
    xml = ('<hierarchy><node class="android.widget.Switch" resource-id="com.app:id/wifi" '
           'checkable="true" checked="true" selected="false" clickable="true" content-desc="" '
           'text="" bounds="[0,0][100,100]"/></hierarchy>')
    n = parse_hierarchy(xml)[0]
    assert n.checkable and n.checked and not n.selected
    plain = parse_hierarchy('<hierarchy><node class="X" resource-id="" clickable="false" '
                            'content-desc="" text="" bounds="[0,0][1,1]"/></hierarchy>')[0]
    assert not plain.checkable and not plain.checked  # defaults preserved


# ---- capture: checkbox tap rides the submit edge ----

def _frame(*kids):
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            + "".join(kids) + "</node></hierarchy>")


def _checkbox(checked):
    return (f'<node class="android.widget.CheckBox" package="com.app" resource-id="com.app:id/terms" '
            f'checkable="true" checked="{str(checked).lower()}" clickable="true" content-desc="" '
            f'text="Terms" bounds="[40,200][1040,300]"/>')


_GO = ('<node class="android.widget.Button" package="com.app" resource-id="com.app:id/go" '
       'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/>')


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def _tap(x, y):
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=x, y=y)


def test_checkbox_tap_rides_submit_edge_not_intra_actions():
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[_frame(_checkbox(False), _GO)] * 3 + [_frame(_checkbox(True), _GO)] * 3
        + [_frame(_GO)] * 3,
        dumpsys_pairs=[_dumpsys(A)] * 3 + [_dumpsys(A)] * 3 + [_dumpsys(B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    a_screen = s.start()
    t1 = s.record_gesture(_tap(540, 250))  # tap the checkbox -> no screen change, but a flip
    assert t1 is None
    assert s.graph.screen(a_screen.id).intra_actions == []  # NOT a probe
    assert len(s._pending) == 1 and s._pending[0][2].action_type == "set_checked"
    t2 = s.record_gesture(_tap(540, 560))  # tap Go -> B (submit)
    assert t2 is not None
    assert [p.action_type for p in t2.pre_actions] == ["set_checked"]
    assert t2.pre_actions[0].value == {"checked": True}


def test_true_noop_still_probe():
    # a tap on a NON-checkable element with no screen change stays an honest probe
    A = "com.app/.A"
    static = _frame('<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/lbl" '
                    'clickable="true" content-desc="" text="hi" bounds="[40,200][1040,300]"/>')
    drv = FakeDriver(hierarchies=[static] * 6, dumpsys_pairs=[_dumpsys(A)] * 6, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    a = s.start()
    s.record_gesture(_tap(540, 250))
    assert s._pending == [] and len(s.graph.screen(a.id).intra_actions) == 1


def test_uncommitted_state_marker_on_stop():
    events = []
    A = "com.app/.A"
    drv = FakeDriver(
        hierarchies=[_frame(_checkbox(False), _GO)] * 3 + [_frame(_checkbox(True), _GO)] * 3,
        dumpsys_pairs=[_dumpsys(A)] * 6, display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, sink=events.append, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 250))  # check the box, then never submit
    s.stop()
    assert any(e.get("event_type") == "uncommitted_state" for e in events)
    assert s._pending == []


# ---- replay: idempotent ----

def _edge_graph(target_checked):
    g = Graph()
    g.upsert_screen(Screen(id="a", namespace="com.app/.A"))
    g.upsert_screen(Screen(id="b", namespace="com.app/.B"))
    g.add_transition(Transition(
        source="a", target="b",
        action=Action(selector=Selector("text", "Go"), action_type="click"),
        pre_actions=[Action(selector=Selector("resource_id", "com.app:id/terms"),
                            action_type="set_checked", value={"checked": target_checked})],
    ))
    return g


def test_set_checked_flips_only_on_mismatch():
    nav = Navigator(_edge_graph(True), FakeDriver(checked_states={"com.app:id/terms": False}))
    res = nav._execute(nav.graph.g["a"]["b"][0]["pre_actions"][0])
    assert res.ok and nav.driver.checked_sets == [("resource_id", "com.app:id/terms", True)]  # real flip


def test_set_checked_idempotent_when_already_target():
    nav = Navigator(_edge_graph(True), FakeDriver(checked_states={"com.app:id/terms": True}))
    nav._execute(nav.graph.g["a"]["b"][0]["pre_actions"][0])
    assert nav.driver.checked_sets[0] == ("resource_id", "com.app:id/terms", True, "noop")  # no flip


def test_set_checked_coords_refused():
    nav = Navigator(Graph(), FakeDriver())
    res = nav._execute(Action(selector=Selector("coords", (5, 5)), action_type="set_checked",
                              value={"checked": True}))
    assert res.ok is False and "coordinate" in res.error


# ---- capture on a VOLATILE form (the BanCoppel fix): set_checked is decoupled from the
# no-op/volatility gate. A never-settling form must STILL capture the checkbox flip. ----

VOLATILE = {"sleep": lambda _dt: None, "max_wait": 0.0}  # settle never converges -> "V" id


def test_volatile_form_checkbox_rides_submit_edge():
    # Previously the checkbox tap fell through the `not either_volatile` gate and was DROPPED.
    # Now detection runs unconditionally: a checkbox flip on a volatile form is a set_checked
    # that rides the submit edge. The flip is text-free, so structure_id (hence the "V" id) is
    # unchanged by it -> source == tgt_id -> Case A.
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[
            _frame(_checkbox(False), _GO),  # start -> volatile S0
            _frame(_checkbox(True), _GO),   # checkbox tap: flipped, SAME structure -> still S0
            _frame(_GO),                    # Go tap: navigated away -> B
        ],
        dumpsys_pairs=[_dumpsys(A), _dumpsys(A), _dumpsys(B)],
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=VOLATILE)
    a_screen = s.start()
    assert a_screen.volatile is True  # the form never settles
    t1 = s.record_gesture(_tap(540, 250))  # tap the checkbox on a VOLATILE screen
    assert t1 is None
    assert s.graph.screen(a_screen.id).intra_actions == []  # NOT demoted to a probe
    assert len(s._pending) == 1 and s._pending[0][2].action_type == "set_checked"
    t2 = s.record_gesture(_tap(540, 560))  # tap Go -> submit
    assert t2 is not None
    assert [p.action_type for p in t2.pre_actions] == ["set_checked"]
    assert t2.pre_actions[0].value == {"checked": True}


_ERR = ('<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/err" '
        'clickable="false" content-desc="" text="Campo requerido" bounds="[40,320][1040,360]"/>')


def test_volatile_jitter_flip_rides_submit_not_edge():
    # The BanCoppel reality: checking the box does NOT advance the screen (a button does), but a
    # VOLATILE form's fingerprint id JITTERS on the tap (a validation row reflows in), so
    # source != tgt_id even though we stayed put. Because the checkbox is still PRESENT and
    # flipped in `after`, the tap did not navigate away from it -> it must ride the submit edge
    # as a set_checked, NOT become a spurious navigating edge.
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[
            _frame(_checkbox(False), _GO),         # start -> S0
            _frame(_checkbox(True), _GO, _ERR),    # checkbox tap: flipped + a row reflowed in -> new id, box still there
            _frame(_GO),                           # Go tap -> navigates to B
        ],
        dumpsys_pairs=[_dumpsys(A), _dumpsys(A), _dumpsys(B)],
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=VOLATILE)
    s.start()
    t1 = s.record_gesture(_tap(540, 250))  # checkbox tap; fingerprint jitters but box stays
    assert t1 is None  # NOT a navigating edge — the box didn't change screens
    assert len(s._pending) == 1 and s._pending[0][2].action_type == "set_checked"
    t2 = s.record_gesture(_tap(540, 560))  # the BUTTON does the advancing
    assert t2 is not None
    assert [p.action_type for p in t2.pre_actions] == ["set_checked"]


def test_checkbox_that_navigates_away_stays_a_click():
    # A tap whose target snapshot NO LONGER CONTAINS the checkbox (the box truly left the
    # screen) is a real navigation: detect_checkable_entry returns None (can't confirm a flip
    # on an absent widget), so it stays an honest navigating click — never a phantom set_checked.
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[
            _frame(_checkbox(False), _GO),  # start -> S0 (box present)
            _frame(_GO),                    # tap -> a screen WITHOUT the checkbox
        ],
        dumpsys_pairs=[_dumpsys(A), _dumpsys(B)],
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=VOLATILE)
    s.start()
    t = s.record_gesture(_tap(540, 250))  # tap where the checkbox was
    assert t is not None
    assert t.action.action_type == "click" and t.pre_actions == []  # honest navigating tap


def test_volatile_pending_drains_by_namespace():
    # On a volatile form the "V"+structure_id jitters as validation rows appear, so the checkbox
    # tap and the submit tap get DIFFERENT ids though they are the same form. The pending
    # set_checked must still drain onto the submit edge via the stable NAMESPACE.
    s = RecordSession(FakeDriver(), PROFILE, settle_kwargs=NOSLEEP)
    s.graph.upsert_screen(Screen(id="Vaaa", namespace="com.app/.Reg", structure_id="Saaa",
                                 volatile=True))
    s.graph.upsert_screen(Screen(id="Vbbb", namespace="com.app/.Reg", structure_id="Sbbb",
                                 volatile=True))
    act = Action(selector=Selector("resource_id", "com.app:id/terms"), action_type="set_checked",
                 value={"checked": True})
    s._pending = [("Vaaa", "com.app/.Reg", act)]
    drained = s._take_pending("Vbbb")  # different volatile id, SAME namespace
    assert [a.action_type for a in drained] == ["set_checked"]
    assert s._pending == []


def test_settled_pending_does_not_overdrain_by_namespace():
    # A SETTLED submit screen matches by id ONLY (never namespace), so two distinct settled
    # screens of one single-Activity app don't steal each other's pending.
    s = RecordSession(FakeDriver(), PROFILE, settle_kwargs=NOSLEEP)
    s.graph.upsert_screen(Screen(id="ida", namespace="com.app/.A", structure_id="S1",
                                 volatile=False))
    s.graph.upsert_screen(Screen(id="idb", namespace="com.app/.A", structure_id="S2",
                                 volatile=False))
    act = Action(selector=Selector("resource_id", "x"), action_type="set_text", value={"text": "y"})
    s._pending = [("ida", "com.app/.A", act)]
    assert s._take_pending("idb") == []  # settled: id-match only, NOT namespace
    assert len(s._pending) == 1


def test_drop_stale_keeps_volatile_same_namespace_pending():
    # #17 launch-then-type: a set_text tagged for the volatile app must SURVIVE entering another
    # volatile fingerprint of the SAME app (the keyboard-down submit screen), so it rides that app's
    # submit edge — while a DIFFERENT app's pending is correctly dropped as stale (uncommitted).
    s = RecordSession(FakeDriver(), PROFILE, settle_kwargs=NOSLEEP)
    s.graph.upsert_screen(Screen(id="Vsub", namespace="com.app/.A", structure_id="Ssub",
                                 volatile=True))
    keep = Action(selector=Selector("resource_id", "com.app:id/f"), action_type="set_text",
                  value={"text": "hi"})
    other = Action(selector=Selector("resource_id", "com.other:id/g"), action_type="set_text",
                   value={"text": "x"})
    s._pending = [("Vtyped", "com.app/.A", keep), ("Vother", "com.other/.B", other)]
    s._drop_stale_pending("Vsub")  # entering the app's submit screen (volatile, same namespace)
    assert [n for (_s, n, _a) in s._pending] == ["com.app/.A"]  # same-app kept, other-app dropped
