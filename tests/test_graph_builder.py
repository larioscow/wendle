"""The GraphBuilder seam (v2 prep): one minter + one commit path, two front-ends.

These tests drive the builder DIRECTLY through the ingest API a future external-crawl
front-end will use — no RecordSession, no gestures — and pin the honesty gates the seam
enforces regardless of front-end. The keystone is the CONVERGENCE LOCK: the same scripted
screens + actions driven once through record_gesture (the human path) and once through the
ingest API produce byte-identical graphs (Graph.to_json equality) — the record→replay
foundation invariant ("record→replay reproduces the capture") made executable for crawl-built graphs.
"""
import pytest

from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.models import Action, DeviceProfile, Selector
from wendle.record.builder import BindContext, GraphBuilder
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
NOSLEEP = {"sleep": lambda _dt: None}
L = ("com.sec.android.app.launcher", ".activities.LauncherActivity")
A = ("com.app", ".AActivity")
B = ("com.app", ".BActivity")


def _screen(pkg, act, rid="ok", label="Go"):
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/{rid}" '
        f'clickable="true" content-desc="" text="{label}" bounds="[40,500][1040,620]"/>'
        "</node></hierarchy>"
    )


def _launcher_xml():
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{L[0]}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.TextView" package="{L[0]}" resource-id="" '
        f'clickable="true" content-desc="App" text="" bounds="[40,500][1040,620]"/>'
        "</node></hierarchy>"
    )


def _ns(pkg, act):
    return f"{pkg}/{act}"


def _builder(events=None):
    b = GraphBuilder(sink=(events.append if events is not None else None))
    b.graph.device_profile = PROFILE
    return b


# ---- minting: the honesty gate is structural (one minter, both front-ends) ----

def test_settled_observation_mints_a_confident_node():
    b = _builder()
    e = b.enter(_screen(*A), _ns(*A), True, A[0])
    s = b.graph.screen(e.id)
    assert not s.volatile and s.fingerprint_confidence == "high"
    assert s.value_bearing is not None  # the L3 bit is computed at mint, not left unknown


def test_unsettled_observation_can_only_mint_a_volatile_node():
    b = _builder()
    e = b.enter(_screen(*A), _ns(*A), False, A[0])
    s = b.graph.screen(e.id)
    assert s.volatile and e.id.startswith("V") and s.fingerprint_confidence == "low"
    assert s.value_bearing is False  # never value-bearing without a settled dump


def test_edge_out_of_a_volatile_source_is_provisional_regardless_of_caller():
    # the builder RECOMPUTES source volatility from the graph — a front-end cannot mint a
    # confident edge out of a volatile node by passing needs_confirmation=False
    b = _builder()
    b.begin(b.enter(_screen(*A), _ns(*A), False, A[0]))     # volatile source
    after = b.enter(_screen(*B), _ns(*B), True, B[0])
    t = b.commit_transition(
        action=Action(selector=Selector("label", "Go"), action_type="click"),
        after=after, bind=BindContext(px=540, py=560, landed=True),
        needs_confirmation=False)
    assert t is not None and t.needs_confirmation is True


# ---- the ingest path end-to-end: launch anchor, edges, classification ----

def test_ingest_path_mints_launch_anchor_and_edge():
    b = _builder()
    b.begin(b.enter(_launcher_xml(), _ns(*L), True, L[0]))
    after = b.enter(_screen(*A), _ns(*A), True, A[0])
    t = b.commit_transition(
        action=Action(selector=Selector("content_desc", "App"), action_type="click",
                      bounds=(40, 500, 1040, 620)),
        after=after, bind=BindContext(px=540, py=560, landed=True))
    assert t is not None
    fa = b.graph.screen(after.id).force_action
    assert fa is not None and fa.kind == "am_start" and fa.provenance == "launcher_entry"
    assert b.current_id == after.id


def test_coordless_swipe_degrades_to_probe_never_reveal():
    # a selector-only crawler (no geometry): the coordinate rungs refuse honestly — a
    # same-screen swipe records as probe, never a replayable reveal
    feed = _feed_xml(["Alpha", "Beta", "Gamma", "Delta"])
    b = _builder()
    b.begin(b.enter(feed, _ns(*A), True, A[0]))
    after = b.enter(feed, _ns(*A), True, A[0])
    t = b.commit_transition(
        action=Action(selector=Selector("coords", (540, 1800)), action_type="swipe",
                      end=(540, 700)),
        after=after, bind=BindContext())  # px=None: no geometry supplied
    assert t is None
    intra = b.graph.screen(b.current_id).intra_actions[-1]
    assert intra.intent == "probe"


def _feed_xml(labels):
    rows = "".join(
        f'<node class="android.view.View" package="com.app" resource-id="" clickable="true" '
        f'checkable="false" focusable="false" content-desc="" text="" scrollable="false" '
        f'bounds="[0,{600 + i * 300}][1080,{900 + i * 300}]">'
        f'<node class="android.widget.TextView" package="com.app" resource-id="" '
        f'clickable="false" checkable="false" focusable="false" content-desc="" text="{lab}" '
        f'scrollable="false" bounds="[40,{610 + i * 300}][1000,{690 + i * 300}]"/></node>'
        for i, lab in enumerate(labels))
    return (
        '<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        '<node class="android.view.View" package="com.app" resource-id="" clickable="false" '
        'checkable="false" focusable="false" content-desc="" text="" scrollable="true" '
        f'bounds="[0,600][1080,2100]">{rows}</node></node></hierarchy>'
    )


def test_with_geometry_the_same_swipe_classifies_reveal():
    feed = _feed_xml(["Alpha", "Beta", "Gamma", "Delta"])
    b = _builder()
    b.begin(b.enter(feed, _ns(*A), True, A[0]))
    after = b.enter(feed, _ns(*A), True, A[0])
    t = b.commit_transition(
        action=Action(selector=Selector("coords", (540, 1800)), action_type="swipe",
                      end=(540, 700)),
        after=after, bind=BindContext(px=540, py=1800, end=(540, 700)))
    assert t is None
    assert b.graph.screen(b.current_id).intra_actions[-1].intent == "reveal"


def test_suspect_tripwire_is_coordinate_free():
    # §2.8 runs off raw before/after dumps — identical for a selector-only crawler
    events = []
    b = _builder(events)
    b.begin(b.enter(_feed_xml(["Alpha", "Beta", "Gamma", "Delta"]), _ns(*A), True, A[0]))
    after = b.enter(_feed_xml(["Echo", "Foxtrot", "Golf", "Hotel"]), _ns(*A), True, A[0])
    assert after.id == b.current_id  # the collapse merge: same id, different content
    t = b.commit_transition(
        action=Action(selector=Selector("label", "Go"), action_type="click"),
        after=after, bind=BindContext())  # NO geometry — the tripwire must still fire
    assert t is not None and t.suspect_self_loop is True
    assert any(e.get("event_type") == "suspect_self_loop" for e in events)


# ---- redaction gate at ingest ----

def test_stage_pending_rejects_a_sensitive_literal():
    events = []
    b = _builder(events)
    bad = Action(selector=Selector("resource_id", "com.app:id/pwd"), action_type="set_text",
                 value={"text": "hunter2"}, sensitive=True)
    assert b.stage_pending(bad, "S0", _ns(*A)) is False
    assert b._pending == []
    assert any(e.get("event_type") == "unreplayable_field" for e in events)
    assert not any("hunter2" in str(e) for e in events)  # the literal never reaches the sink
    ok = Action(selector=Selector("resource_id", "com.app:id/pwd"), action_type="set_text",
                value={"param": "password"}, sensitive=True)
    assert b.stage_pending(ok, "S0", _ns(*A)) is True


# ---- rename repair through the ingest surface ----

def test_remap_holders_repairs_every_id_holder_and_notifies_front_end():
    seen = []
    b = GraphBuilder(on_rename=seen.append)
    b.current_id = "OLD"
    b._pending = [("OLD", "com.app/.A", Action(selector=Selector("label", "x"),
                                               action_type="set_text",
                                               value={"param": "user"}, sensitive=True))]
    b.provisional = ["OLD->T#0"]
    b.remap_holders({"OLD": "NEW"}, {("OLD", "T", 0): ("NEW", "T", 3)})
    assert b.current_id == "NEW"
    assert b._pending[0][0] == "NEW"          # the staged credential survived the rename
    assert b.provisional == ["NEW->T#3"]      # with the REAL re-added edge key
    assert seen == [{"OLD": "NEW"}]           # the front-end can repair its private state


# ---- THE CONVERGENCE LOCK: human path == ingest path, byte-identical ----

def test_record_and_ingest_produce_byte_identical_graphs():
    frames = [(_launcher_xml(), L), (_screen(*A), A), (_screen(*B, rid="next"), B)]

    # human path: RecordSession over FakeDriver + gestures
    hs, ds = [], []
    for xml, (pkg, act) in frames:
        hs += [xml] * 3
        ds += [(f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
                f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")] * 3
    sess = RecordSession(FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340)),
                         PROFILE, settle_kwargs=NOSLEEP)
    sess.start()
    sess.record_gesture(Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560))  # L -> A
    sess.record_gesture(Gesture(kind="tap", t_down=2.0, t_up=2.05, x=540, y=560))  # A -> B
    human = sess.graph.to_json()

    # ingest path: the same frames + the equivalent reported Actions, no recorder at all
    b = _builder()
    b.begin(b.enter(*_obs(frames[0])))
    a1 = b.enter(*_obs(frames[1]))
    b.commit_transition(
        action=Action(selector=Selector("content_desc", "App"), action_type="click",
                      replayability="high"),
        after=a1, bind=BindContext(px=540, py=560, bounds=(40, 500, 1040, 620), landed=True))
    a2 = b.enter(*_obs(frames[2]))
    b.commit_transition(
        action=Action(selector=Selector("label", "Go"), action_type="click",
                      replayability="high"),
        after=a2, bind=BindContext(px=540, py=560, bounds=(40, 500, 1040, 620), landed=True))
    crawl = b.graph.to_json()

    assert human == crawl  # one minter, one commit path -> the SAME graph, byte for byte


def _obs(frame):
    xml, (pkg, act) = frame
    return xml, f"{pkg}/{act}", True, pkg


def test_fork_hop_convergence_with_geometry_and_documented_degrade_without():
    """Extends the lock to the Cap 1 scroll-mint path (review finding: the original lock
    covered only tap/launch edges, hiding a fork-hop divergence). WITH full geometry — the
    plan's recommended crawler integration (element bounds are cheap via .info) — the two
    front-ends stay byte-identical, scroll edge included. WITHOUT geometry the §2.7 rungs
    cannot prove a reveal, so the hop degrades to a navigate edge — the design's DOCUMENTED
    honest direction (a dropped hop classification, never a wrong reveal); this test pins
    that degrade shape so it can't drift silently."""
    from tests.test_fork_twin_routing import NS as F_NS, PKG as F_PKG, XML_SCR, XML_TOP

    hs = [XML_TOP] * 3 + [XML_SCR] * 3
    ds = [(f"topResumedActivity: ActivityRecord{{x u0 {F_NS} t1}}",
           f"mCurrentFocus=Window{{x u0 {F_NS}}}")] * 6
    sess = RecordSession(FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340)),
                         PROFILE, settle_kwargs=NOSLEEP)
    sess.start()
    sess.record_gesture(Gesture(kind="swipe", t_down=1.0, t_up=1.3,
                                x=540, y=1700, x2=540, y2=700))
    human = sess.graph.to_json()
    assert any(d.get("action_class") == "scroll"
               for (_u, _v, _k, d) in sess.graph.ordered_transitions())

    def ingest(bind):
        b = _builder()
        b.begin(b.enter(XML_TOP, F_NS, True, F_PKG))
        after = b.enter(XML_SCR, F_NS, True, F_PKG)
        b.commit_transition(
            action=Action(selector=Selector("coords", (540, 1700)), action_type="swipe",
                          end=(540, 700), replayability="coordinate_only"),
            after=after, bind=bind)
        return b.graph

    with_geo = ingest(BindContext(px=540, py=1700, end=(540, 700),
                                  bounds=(0, 1500, 1080, 1800), landed=True))
    assert with_geo.to_json() == human  # geometry-supplied crawler: byte-identical, fork incl.

    without_geo = ingest(BindContext())  # selector-only crawler: no geometry
    degraded = list(without_geo.ordered_transitions())
    assert all(d.get("action_class") != "scroll" for (_u, _v, _k, d) in degraded), \
        "without geometry the rungs must refuse to PROVE a reveal (honest degrade)"
    assert without_geo.to_json() != human  # the divergence is real — hence the bounds advice
