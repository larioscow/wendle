"""Text entry wired into the live recorder: keystroke suppression + multi-field set_text."""
from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.types import Gesture, Snapshot
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.compose import resolve_profile
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession, _profile_name

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
NOSLEEP = {"sleep": lambda _dt: None}


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def _btn(ns):
    pkg = ns.split("/")[0]
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/go" '
        'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/></node></hierarchy>'
    )


# A REALISTIC Gboard key: cls=android.inputmethodservice.*, EMPTY resource-id, label in `text`.
def _gboard_key(label, x):
    return (
        f'<node class="android.inputmethodservice.Keyboard$Key" resource-id="" clickable="true" '
        f'content-desc="" text="{label}" bounds="[{x},2000][{x+90},2090]"/>'
    )


def _field(text, ns="com.app/.A", rid="user", password=False):
    pkg = ns.split("/")[0]
    pw = ' password="true"' if password else ''
    rid_attr = f'{pkg}:id/{rid}' if rid else ''
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.EditText" package="{pkg}" resource-id="{rid_attr}" '
        f'focused="true"{pw} clickable="true" content-desc="" text="{text}" bounds="[40,200][1040,300]"/>'
        + _gboard_key("a", 40) + _gboard_key("s", 160) + "</node></hierarchy>"
    )


def _fresh(xml, ns="com.app/.A", stable=2):
    cfg = resolve_profile(xml, ns)
    return {"ns": ns, "snap": Snapshot(0.0, 0.0, "", parse_hierarchy(xml)),
            "id": fingerprint(ns, xml, cfg), "struct": structure_id(ns, xml),
            "profile_name": _profile_name(cfg, ns), "focus": ns.split("/")[0], "stable": stable}


def _tap(x, y):
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=x, y=y)


def _session_on(ns):
    drv = FakeDriver(hierarchies=[_btn(ns)] * 3, dumpsys_pairs=[_dumpsys(ns)] * 3, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    return s


# ---- SECURITY: keystroke suppression on a realistic keyboard ----

def test_realistic_gboard_key_tap_suppressed():
    # Gboard keys: empty resource-id, cls=inputmethodservice, label in text. Must STILL be
    # suppressed (the resource-id-only check missed these -> password leaked char-by-char).
    s = _session_on("com.app/.A")
    s._fresh = _fresh(_field("al"))
    assert s.record_gesture(_tap(85, 2045)) is None  # tap the 'a' key -> swallowed
    assert s.graph.g.number_of_edges() == 0


def test_app_element_above_keyboard_outside_ime_region():
    # the IME region covers only the keyboard; app controls above it (field, submit
    # button, results) are NOT suppressed.
    s = _session_on("com.app/.A")
    ime = s._ime_bounds(parse_hierarchy(_field("al")))
    assert ime is not None
    assert not (ime[1] <= 250 <= ime[3])  # the field at y~250 is ABOVE the keyboard
    assert ime[1] <= 2045 <= ime[3]       # a key at y~2045 is inside


def _generic_keyboard_field(text):
    # modern Gboard: the IME window root is inputmethodservice, but the KEYS are generic
    # android.view.View with empty resource-id and the label in content-desc.
    keys = "".join(
        f'<node class="android.view.View" resource-id="" clickable="true" content-desc="{c}" '
        f'text="" bounds="[{40 + i*90},2050][{120 + i*90},2140]"/>'
        for i, c in enumerate("hunter")
    )
    return (
        '<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        '<node class="android.widget.EditText" package="com.app" resource-id="com.app:id/pw" '
        f'focused="true" password="true" clickable="true" content-desc="" text="{text}" bounds="[40,200][1040,300]"/>'
        '<node class="android.inputmethodservice.SoftInputWindow" '
        'package="com.google.android.inputmethod.latin" resource-id="" clickable="false" '
        f'content-desc="" text="" bounds="[0,1990][1080,2340]">{keys}</node>'
        '</node></hierarchy>'
    )


def test_keystroke_suppressed_right_after_navigation_no_fresh():
    # the ~refresh-interval hole: after navigating to a keyboard screen, _fresh is None;
    # suppression must fall back to the arrival snapshot (current_snapshot), not leak.
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[_btn(A)] * 3 + [_field("", ns=B)] * 3,
        dumpsys_pairs=[_dumpsys(A)] * 3 + [_dumpsys(B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 560))  # A -> B (B has a focused field + keyboard)
    assert s._fresh is None  # refresher off in tests -> the dangerous None window
    n = s.graph.g.number_of_edges()
    assert s.record_gesture(_tap(85, 2045)) is None  # key tap on B -> suppressed via current_snapshot
    assert s.graph.g.number_of_edges() == n


def test_generic_class_keys_suppressed_by_region():
    # the security regression the rework review caught: generic-View keys still leak unless
    # suppression keys on the keyboard REGION (the inputmethodservice window root) not class.
    s = _session_on("com.app/.A")
    s._fresh = _fresh(_generic_keyboard_field("hun"))
    assert s.record_gesture(_tap(85, 2090)) is None  # tap a generic key in the IME region
    assert s.graph.g.number_of_edges() == 0


# ---- SECURITY: the IME-node identity rule itself (package / rid namespace / framework
# class — never a package marker matched against an app's class path) ----

def _node(**kw):
    from wendle.capture.types import UINode
    return UINode(cls=kw.get("cls", "android.widget.Button"), resource_id=kw.get("rid", ""),
                  text="", content_desc="", clickable=True, password=False,
                  bounds=(0, 0, 10, 10), package=kw.get("pkg", ""))


def test_is_ime_node_flags_keyboard_by_package_rid_and_framework_class():
    assert RecordSession._is_ime_node(_node(pkg="com.google.android.inputmethod.latin"))
    assert RecordSession._is_ime_node(_node(rid="com.google.android.inputmethod.latin:id/key_a"))
    assert RecordSession._is_ime_node(_node(cls="android.inputmethodservice.Keyboard$Key"))


def test_is_ime_node_does_not_flag_app_class_with_marker_substring():
    # finding 7's suppression-side twin: an app control whose class merely contains '.ime'
    # must NOT join the keyboard region — its bounds would silently swallow real app taps.
    assert not RecordSession._is_ime_node(
        _node(cls="com.bank.imexpress.SubmitButton", rid="com.bank:id/go", pkg="com.bank"))


# ---- multi-field login: one set_text per field, all on the submit edge ----

def _typed(text, rid, password=False, ns="com.app/.A"):
    return _fresh(_field(text, ns=ns, rid=rid, password=password), ns)


def test_field_switch_finalizes_previous_field():
    s = _session_on("com.app/.A")
    s._track_text(None, _typed("", "user"))
    s._track_text(_typed("", "user"), _typed("alice", "user"))
    s._track_text(_typed("alice", "user"), _typed("", "pw"))  # switch user -> pw
    assert len(s._pending) == 1 and s._pending[0][2].value == {"text": "alice"}


def test_typing_right_after_launch_tags_to_field_screen_not_stale_current_id():
    # #17 MIS-TAG: the refresher detects a field on a freshly-launched APP while the main thread's
    # current_id still LAGS on the previous screen (the launcher). The set_text must be tagged with
    # the FIELD's own fresh namespace (new["ns"]), so _take_pending's namespace bridge drains it onto
    # the APP's submit edge — not stranded on the launcher namespace where it never matches and the
    # typing is silently dropped (the multi-app dropped-typing bug). Was tagged to self.current_id.
    PREV, APP = "com.sec.android.app.launcher/.Home", "com.app/.A"
    s = _session_on(PREV)  # current_id lags here while the user already typed in APP
    s._track_text(None, _typed("", "chat", ns=APP))
    s._track_text(_typed("", "chat", ns=APP), _typed("hi", "chat", ns=APP))
    s._commit_in_flight()
    assert len(s._pending) == 1
    _screen, ns, action = s._pending[0]
    assert action.value == {"text": "hi"}
    assert ns == APP  # the field's app namespace, NOT the stale launcher (PREV) it was tagged to


def test_multi_field_both_set_texts_ride_on_submit_edge():
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[_btn(A)] * 3 + [_btn(B)] * 3,
        dumpsys_pairs=[_dumpsys(A)] * 3 + [_dumpsys(B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()  # current = A (the login screen)
    src = s.current_id
    # user typed, then password typed (refresher would drive this)
    s._track_text(None, _typed("", "user"))
    s._track_text(_typed("", "user"), _typed("alice", "user"))
    s._track_text(_typed("alice", "user"), _typed("", "pw", password=True))   # finalize user
    s._track_text(_typed("", "pw", password=True), _typed("hunter2", "pw", password=True))
    s._fresh = _fresh(_btn(A), A)  # at submit the keyboard is gone -> not suppressed
    t = s.record_gesture(_tap(540, 560))  # tap Go -> B (submit)
    assert t is not None and len(t.pre_actions) == 2
    assert t.pre_actions[0].value == {"text": "alice"}
    assert t.pre_actions[1].sensitive and "param" in t.pre_actions[1].value
    assert "hunter2" not in s.graph.to_json()  # password literal never persisted


def test_typed_submit_not_deduped_away():
    # a bare 'Login' edge already exists; a later TYPED submit must NOT be dropped by dedup
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[_btn(A)] * 3 + [_btn(B)] * 3 + [_btn(B)] * 3,
        dumpsys_pairs=[_dumpsys(A)] * 3 + [_dumpsys(B)] * 3 + [_dumpsys(B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 560))  # A->B bare (no typing)
    # back on A, now with a pending typed field
    src = next(n for n in s.graph.g.nodes if s.graph.screen(n).namespace == A)
    s._pending = [(src, A, __import__("wendle.models", fromlist=["Action", "Selector"]).Action(
        selector=__import__("wendle.models", fromlist=["Selector"]).Selector("resource_id", "com.app:id/user"),
        action_type="set_text", value={"text": "alice"}))]
    s.current_id = src
    s.current_snapshot = Snapshot(0.0, 0.0, "", parse_hierarchy(_btn(A)))
    t = s.record_gesture(_tap(540, 560))  # A->B again, but typed
    assert t is not None and len(t.pre_actions) == 1  # NOT deduped away
    assert s.graph.g.number_of_edges(src, t.target) == 2  # bare + typed both kept


def test_coords_field_emits_unreplayable_marker():
    events = []
    A = "com.app/.A"
    drv = FakeDriver(hierarchies=[_btn(A)] * 6, dumpsys_pairs=[_dumpsys(A)] * 6, display=(1080, 2340))
    s = RecordSession(drv, PROFILE, sink=events.append, settle_kwargs=NOSLEEP)
    s.start()
    s._typing_before = Snapshot(0.0, 0.0, "", parse_hierarchy(_field("", rid="", password=True)))
    s._typing_after = Snapshot(0.0, 0.0, "", parse_hierarchy(_field("secret", rid="", password=True)))
    s._commit_in_flight()
    assert s._pending == []
    assert any(e.get("event_type") == "unreplayable_field" for e in events)


def test_live_refresh_false_no_typing_path():
    # the 240+ existing tests run with live_refresh=False (no _fresh) -> typing path inert
    s = _session_on("com.app/.A")
    assert s._fresh is None
    t = s.record_gesture(_tap(540, 560))  # plain tap, no field
    assert t is None or t.pre_actions == []  # no spurious pre_actions


def test_keystroke_suppressed_when_only_arrival_frame_shows_the_keyboard():
    # arbitration decoupling (adversarial #3 / invariant #4): the live refresher lagged on a
    # same-namespace mid-load frame WITHOUT the keyboard while the arrival snapshot HAS it
    # (keyboard up). A key tap must STILL be suppressed — suppression errs across EVERY
    # faithful view, not only _fresh (else a password key leaks as a click edge).
    A, B = "com.app/.A", "com.app/.B"
    drv = FakeDriver(
        hierarchies=[_btn(A)] * 3 + [_generic_keyboard_field("")] * 3,
        dumpsys_pairs=[_dumpsys(A)] * 3 + [_dumpsys(B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(540, 560))  # A -> B (B has a focused password field + keyboard)
    midload_no_kb = ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" '
                     'resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
                     '<node class="android.widget.FrameLayout" package="com.app" '
                     'resource-id="com.app:id/loading" clickable="false" content-desc="" text="" '
                     'bounds="[0,200][1080,1980]"/></node></hierarchy>')
    s._fresh = _fresh(midload_no_kb, ns=B, stable=0)  # refresher lagged: no keyboard in this frame
    n = s.graph.g.number_of_edges()
    assert s.record_gesture(_tap(85, 2095)) is None  # key tap -> suppressed via the arrival frame
    assert s.graph.g.number_of_edges() == n
    # and it must NOT leak anywhere else — the effectiveness filter would otherwise record the
    # key as an intra-screen 'probe' (a password char in the graph) if suppression is skipped.
    leaked = [a.selector for nid in s.graph.g.nodes
              for a in getattr(s.graph.screen(nid), "intra_actions", [])]
    assert leaked == [], f"keystroke leaked into the graph: {leaked}"
