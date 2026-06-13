"""§2.5/§2.6 of the lazy-region design: value-bearing guard re-keying + the version gate.

L3: on-sight confidence keys on the RECORDED fact that outside-region values entered the
hash — never on the include_text profile alone. §2.6: a graph recorded under an older
identity version is refused TYPED and INSTANTLY, never replayed into an off_graph storm.
"""
import json

import pytest

from wendle import StaleRecordingError, cli
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import SIGNATURE_VERSION
from wendle.graph import Graph, check_signature_version
from wendle.models import Action, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator
from wendle.replay.engine import ReplayEngine, replay_recording


def _graph(value_bearing=None):
    g = Graph()
    g.upsert_screen(Screen(id="C0", namespace="app/.A", package="app", activity=".A",
                           structure_id="Sxx", profile_name="compose",
                           value_bearing=value_bearing))
    g.upsert_screen(Screen(id="C1", namespace="app/.B", package="app", activity=".B",
                           profile_name="compose"))
    g.add_transition(Transition(source="C0", target="C1",
                                action=Action(selector=Selector("text", "Go"),
                                              action_type="click"),
                                suspect_self_loop=True))
    return g


# ---- serialization round-trip ----

def test_new_fields_and_version_round_trip():
    g = _graph(value_bearing=True)
    g2 = Graph.from_json(g.to_json())
    assert g2.signature_version == SIGNATURE_VERSION
    assert g2.screen("C0").value_bearing is True
    assert g2.screen("C1").value_bearing is None  # tri-state survives
    (_, _, _, data), = list(g2.ordered_transitions())
    assert data["suspect_self_loop"] is True


def test_legacy_json_reads_as_version_1():
    blob = json.loads(_graph().to_json())
    del blob["signature_version"]
    g = Graph.from_json(json.dumps(blob))
    assert g.signature_version == 1
    assert g.screen("C0").value_bearing is None or g.screen("C0").value_bearing is False


# ---- the version gate: typed instant refusal on BOTH verbs ----

def test_replay_refuses_stale_recording():
    g = _graph()
    g.signature_version = 1
    with pytest.raises(StaleRecordingError, match="re-record"):
        replay_recording(g, FakeDriver())


def test_navigate_refuses_stale_recording():
    g = _graph()
    g.signature_version = 1
    nav = Navigator(g, FakeDriver())
    with pytest.raises(StaleRecordingError, match="signature_version 1"):
        nav.navigate("C0", "C1")


def test_engine_refuses_before_touching_the_device():
    g = _graph()
    g.signature_version = 1
    drv = FakeDriver()
    with pytest.raises(StaleRecordingError):
        ReplayEngine(g, drv).run()
    assert drv.taps == [] and drv.app_starts == []  # refused INSTANTLY, device untouched


def test_current_graph_passes_the_gate():
    check_signature_version(_graph())  # no raise


def test_cli_maps_stale_recording_to_usage_error(tmp_path, capsys):
    blob = json.loads(_graph().to_json())
    blob["signature_version"] = 1
    p = tmp_path / "old.json"
    p.write_text(json.dumps(blob))
    assert cli.main(["replay", str(p)]) == 2
    assert "re-record" in capsys.readouterr().err


# ---- L3: the two on-sight guards key on the recorded bit ----

@pytest.fixture()
def nav():
    return Navigator(_graph(), FakeDriver())


def _compose_screen(value_bearing, **kw):
    return Screen(id="X", namespace="app/.A", structure_id="Sxx",
                  profile_name="compose", value_bearing=value_bearing, **kw)


def test_on_sight_confidence_requires_recorded_value_evidence(nav):
    assert nav._value_bearing_on_sight(_compose_screen(True), "<x/>", "app") is True
    assert nav._value_bearing_on_sight(_compose_screen(False), "<x/>", "app") is False
    assert nav._value_bearing_on_sight(_compose_screen(None), "<x/>", "app") is False  # legacy


def test_refined_twin_path_unchanged_by_value_bearing(nav):
    s = _compose_screen(False, coarse_id="Cxx")
    assert nav._value_bearing_on_sight(s, "<x/>", "app") is True  # digest-distinguished twin
    s2 = _compose_screen(False, coarse_id="Cxx", adapter_dominant=True)
    assert nav._value_bearing_on_sight(s2, "<x/>", "app") is False  # HW2 guard intact


def test_fingerprint_ambiguity_gated_on_value_bearing(nav):
    G = nav.graph.routable_subgraph()
    assert nav._fingerprint_ambiguous(_compose_screen(True), G) is False
    # value_bearing False + a recorded structure twin sharing the id -> ambiguous
    nav.graph.upsert_screen(Screen(id="TWIN", namespace="app/.A", structure_id="Sxx",
                                   profile_name="compose"))
    G2 = nav.graph.routable_subgraph()
    assert nav._fingerprint_ambiguous(_compose_screen(False), G2) is True
    assert nav._fingerprint_ambiguous(_compose_screen(None), G2) is True  # legacy = not proven


# ---- §2.8: arrival at a node carrying a suspect self-loop is NEVER confident ----
# (The recorder only ever emits a suspect edge as a SELF-LOOP on the ambiguous node; an edge
#  between two different nodes — the pre-fix test shape — is unreachable, since the navigator
#  routes over routable_subgraph which drops self-loops. The cap keys on the TARGET node.)

def _suspect_walk_case(suspect: bool):
    from wendle.fingerprint.compose import COMPOSE_PROFILE, VIEW_PROFILE
    from wendle.fingerprint.signature import (
        fingerprint, outside_region_value_bearing, structure_id)
    from wendle.models import ForceAction

    ns_h, ns_t = "app/.Home", "app/.Wizard"
    h_xml = ('<hierarchy><node class="android.widget.LinearLayout" resource-id="app:id/home" '
             'clickable="false" content-desc=""><node class="android.widget.Button" '
             'resource-id="app:id/go" clickable="true" content-desc="" text="go"/></node></hierarchy>')
    t_xml = ('<hierarchy><node class="androidx.compose.ui.platform.AndroidComposeView" '
             'resource-id="" clickable="false" content-desc=""><node class="android.view.View" '
             'resource-id="" clickable="false" content-desc="" text="Pick interests"/>'
             '</node></hierarchy>')
    h = Screen(id=fingerprint(ns_h, h_xml, VIEW_PROFILE), namespace=ns_h,
               structure_id=structure_id(ns_h, h_xml), package="app", activity=".Home",
               profile_name="view",
               force_action=ForceAction("am_start", ns_h, verified_fp="seed"))
    t_vb = outside_region_value_bearing(t_xml, COMPOSE_PROFILE)
    assert t_vb is True  # WITHOUT the suspect cap this target is confident ON SIGHT
    t = Screen(id=fingerprint(ns_t, t_xml, COMPOSE_PROFILE), namespace=ns_t,
               structure_id=structure_id(ns_t, t_xml), package="app", activity=".Wizard",
               profile_name="compose", value_bearing=t_vb)
    g = Graph()
    g.upsert_screen(h)
    g.upsert_screen(t)
    g.add_transition(Transition(source=h.id, target=t.id,
                                action=Action(selector=Selector("text", "go"),
                                              action_type="click")))
    if suspect:
        # the recorder's real shape: a suspect SELF-LOOP on the ambiguous (arrived) node
        g.add_transition(Transition(source=t.id, target=t.id, suspect_self_loop=True,
                                    action=Action(selector=Selector("text", "next"),
                                                  action_type="click")))
    t_clock = [0.0]
    nav = Navigator(g, FakeDriver(present_selectors={("text", "go")}),
                    clock=lambda: t_clock[0],
                    sleep=lambda dt: t_clock.__setitem__(0, t_clock[0] + dt))
    seq = [(h_xml, ns_h, "app", True), (t_xml, ns_t, "app", True)]
    nav._observe = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
    return nav.navigate(h.id, t.id)


def test_arrival_at_suspect_node_is_never_confident():
    assert _suspect_walk_case(suspect=False).status == "arrived"  # control: normal node
    out = _suspect_walk_case(suspect=True)
    assert out.status != "arrived"  # the cap defeats even on-sight value-bearing confidence
    assert out.status in ("arrived_unverified", "off_graph", "force_failed")
