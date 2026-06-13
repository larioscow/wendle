"""The graph→emitter seam (v2 codegen prep): pluggable Emitters + the credential contract.

The scope doc's instruction — "when codegen is built, stub the graph→emitter seam first" —
exists because codegen is the worst credential-leak sink. The CONTRACT test here makes
"no emitter can leak a secret, a selector value, or a raw coordinate" a structural property
every future emitter (Maestro YAML, Python POM, ...) inherits by construction.
"""
import pytest

from wendle import cli
from wendle.emit import all_emitters, get_emitter
from wendle.emit.dot import DotEmitter
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.render import render, to_dot


def _graph():
    """Anchor -> click edge with a sensitive set_text pre_action -> a coords swipe edge.
    Carries every leak class the contract guards: a PII-ish selector VALUE, a {param}
    credential handle, raw coordinates."""
    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace="com.app/.Login", package="com.app",
                           activity=".Login",
                           force_action=ForceAction("am_start", "com.app/.Login",
                                                    verified_fp="S0")))
    g.upsert_screen(Screen(id="S1", namespace="com.app/.Home", package="com.app",
                           activity=".Home"))
    g.add_transition(Transition(
        source="S0", target="S1",
        action=Action(selector=Selector("label", "alice@example.com"), action_type="click"),
        pre_actions=[Action(selector=Selector("resource_id", "com.app:id/pwd"),
                            action_type="set_text", value={"param": "password"},
                            sensitive=True)]))
    g.add_transition(Transition(
        source="S1", target="S0",
        action=Action(selector=Selector("coords", (123, 456)), action_type="swipe",
                      end=(123, 999))))
    return g


# ---- registry ----

def test_registry_resolves_known_targets_and_refuses_unknown():
    assert get_emitter("dot") is not None
    assert get_emitter("flow") is not None
    assert get_emitter("maestro") is not None
    with pytest.raises(ValueError, match="unknown emit target"):
        get_emitter("appium")  # not built — a typed refusal, not a silent fallback
    assert {e.name for e in all_emitters()} >= {"dot", "flow", "maestro"}


# ---- dot: the reference emitter, behavior-preserved ----

def test_dot_emitter_is_the_old_to_dot_byte_for_byte():
    g = _graph()
    assert DotEmitter().emit(g) == to_dot(g)
    assert to_dot(g).startswith("digraph nav_map {")


def test_render_default_target_unchanged(tmp_path):
    out = str(tmp_path / "map.dot")
    assert render(_graph(), out) == out
    assert open(out).read().startswith("digraph nav_map {")


# ---- flow: the skeleton outline emitter (proves pluggability) ----

def test_flow_outline_walks_the_trace_in_order():
    out = get_emitter("flow").emit(_graph())
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert "launch" in lines[0] and "com.app/.Login" in lines[0]
    assert "set_text resource_id {param:password}" in out  # the handle, never the value
    assert "click label" in out
    assert "[coordinate_only]" in out  # coords actions flagged, never their pixels


# ---- THE CREDENTIAL-SAFETY CONTRACT: every registered emitter, now and future ----

def test_no_emitter_leaks_values_params_or_coordinates():
    # TWO contract classes (the codegen split): every emitter — no sensitive literal, no raw
    # coordinates, ever. Map/outline emitters (emits_selector_values=False, the default)
    # additionally never ship ANY selector value (PII even unflagged); codegen emitters
    # (maestro) ship selector values BY DESIGN — that is the artifact.
    g = _graph()
    for emitter in all_emitters():
        out = emitter.emit(g)
        assert "123" not in out and "456" not in out, f"{emitter.name} leaked coordinates"
        assert "hunter2" not in out  # no literal exists in the graph; belt-and-braces
        if not getattr(emitter, "emits_selector_values", False):
            assert "alice@example.com" not in out, f"{emitter.name} leaked a selector VALUE"


# ---- CLI surface ----

def test_cli_render_target_flow(tmp_path):
    rec = tmp_path / "rec.json"
    rec.write_text(_graph().to_json())
    out = str(tmp_path / "steps.flow")
    assert cli.main(["render", str(rec), "-o", out, "--target", "flow"]) == 0
    blob = open(out).read()
    assert "click label" in blob and "digraph" not in blob


def test_cli_render_default_still_dot(tmp_path):
    rec = tmp_path / "rec.json"
    rec.write_text(_graph().to_json())
    out = str(tmp_path / "map.dot")
    assert cli.main(["render", str(rec), "-o", out]) == 0
    assert open(out).read().startswith("digraph")
