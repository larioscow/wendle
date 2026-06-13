"""The Maestro emitter — v2 codegen pillar 1 on the emit/ seam.

Emits a RUNNABLE Maestro flow mirroring the engine's own command derivation (launch_anchor +
flow_from_recording — same as FlowEmitter, so what Maestro runs is what replay runs). Codegen
emitters are a DISTINCT contract class: they ship selector values BY DESIGN (that is the
product), but the hard line holds — NEVER a sensitive typed value (only ${param} handles),
NEVER raw coordinates (refusal comments), NEVER a blind set_checked tap (state-inverting).
"""
import pytest

from wendle import cli
from wendle.emit import all_emitters, get_emitter
from wendle.fingerprint.signature import structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition

PKG = "com.app"


def _graph():
    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace=f"{PKG}/.Login", package=PKG, activity=".Login",
                           force_action=ForceAction("am_start", f"{PKG}/.Login",
                                                    verified_fp="S0")))
    for sid, act in (("S1", ".Home"), ("S2", ".List"), ("S3", ".Deep"), ("S4", ".End")):
        g.upsert_screen(Screen(id=sid, namespace=f"{PKG}/{act}", package=PKG, activity=act))
    # submit with a sensitive credential pre_action + a set_checked pre_action
    g.add_transition(Transition(
        source="S0", target="S1",
        action=Action(selector=Selector("label", "Iniciar sesión"), action_type="click"),
        pre_actions=[
            Action(selector=Selector("resource_id", "com.app:id/pwd"), action_type="set_text",
                   value={"param": "password"}, sensitive=True),
            Action(selector=Selector("resource_id", "com.app:id/remember"),
                   action_type="set_checked", value={"checked": True}),
        ]))
    # a chrome-fork scroll edge (replay skips; maestro bridges with scrollUntilVisible)
    g.add_transition(Transition(
        source="S1", target="S2", action_class="scroll",
        action=Action(selector=Selector("coords", (540, 1800)), action_type="swipe",
                      end=(540, 700), intent="reveal", in_region=True)))
    # post-scroll tap by content-desc; then a long-press by resource id
    g.add_transition(Transition(
        source="S2", target="S3",
        action=Action(selector=Selector("content_desc", "Perfil"), action_type="click")))
    g.add_transition(Transition(
        source="S3", target="S4",
        action=Action(selector=Selector("resource_id", "com.app:id/item"),
                      action_type="long_click")))
    # a coordinate-only tap: must REFUSE in codegen, never emit pixels
    g.add_transition(Transition(
        source="S4", target="S0",
        action=Action(selector=Selector("coords", (333, 444)), action_type="click",
                      replayability="coordinate_only")))
    return g


def test_registry_has_maestro_and_cli_emits_it(tmp_path):
    assert get_emitter("maestro").name == "maestro"
    rec = tmp_path / "rec.json"
    rec.write_text(_graph().to_json())
    out = str(tmp_path / "flow.yaml")
    assert cli.main(["render", str(rec), "-o", out, "--target", "maestro"]) == 0
    assert "launchApp" in open(out).read()


def test_flow_shape_mirrors_replay_semantics():
    out = get_emitter("maestro").emit(_graph())
    assert out.startswith(f"appId: {PKG}\n")
    assert "- launchApp" in out
    # credential: tap the field by its stable id, type the ${param} handle — never a literal
    assert 'id: "com.app:id/pwd"' in out
    assert "- inputText: ${password}" in out
    # set_checked: REFUSED with a comment (a blind tap can invert state), never a tapOn
    assert "set_checked" in out and 'id: "com.app:id/remember"' not in out.replace(
        "# ", "")  # appears only inside the refusal comment, never as a command
    # label union -> Maestro text shorthand; content-desc -> description; rid -> id
    assert '- tapOn: "Iniciar sesión"' in out
    assert 'description: "Perfil"' in out
    assert "- longPressOn:" in out and 'id: "com.app:id/item"' in out
    # the scroll edge becomes scrollUntilVisible targeting the NEXT step's selector
    assert "- scrollUntilVisible:" in out and "Perfil" in out.split("- scrollUntilVisible:")[1]


def test_codegen_hard_lines_no_secrets_no_coordinates():
    out = get_emitter("maestro").emit(_graph())
    assert "hunter2" not in out          # no sensitive literal exists anywhere
    assert "333" not in out and "444" not in out  # coords tap refused, pixels never emitted
    assert "540" not in out and "1800" not in out  # scroll gesture pixels never emitted
    assert "wendle:refused" in out          # the refusal is explicit, not a silent drop


def test_contract_classes_codegen_vs_redaction_safe():
    # codegen emitters declare emits_selector_values=True; map/outline emitters stay fully
    # value-free. BOTH classes: never a sensitive literal, never raw coordinates.
    g = _graph()
    for emitter in all_emitters():
        out = emitter.emit(g)
        assert "hunter2" not in out, f"{emitter.name}: sensitive literal"
        assert "333" not in out and "1800" not in out, f"{emitter.name}: coordinates"
        if not getattr(emitter, "emits_selector_values", False):
            assert "Iniciar sesión" not in out, f"{emitter.name}: selector value in a value-free emitter"


def test_maestro_emits_a_tabbed_app_flow():
    # codegen on a single-Activity tabbed map: each global-nav tab switch emits a runnable
    # tapOn-by-description (Maestro matches content-desc natively; no identity machinery needed,
    # and no coords/secret leak). global_affordance metadata needs no special emitter handling.
    from wendle.fingerprint.signature import structure_id
    g = Graph()
    NS = f"{PKG}/.Main"
    g.upsert_screen(Screen(id="HOME", namespace=NS, package=PKG, activity=".Main",
                           force_action=ForceAction("am_start", NS, verified_fp="HOME")))
    g.upsert_screen(Screen(id="WORLD", namespace=NS, package=PKG, activity=".Main"))
    g.add_transition(Transition(source="HOME", target="WORLD",
                                action=Action(selector=Selector("content_desc", "Reloj mundial"),
                                              action_type="click", bounds=(500, 2800, 680, 2980)),
                                global_affordance=True))
    out = get_emitter("maestro").emit(g)
    assert "- launchApp" in out
    assert 'description: "Reloj mundial"' in out   # the tab switch is a runnable tapOn
    assert "coordinate" not in out and "[2800]" not in out and "wendle:refused" not in out
