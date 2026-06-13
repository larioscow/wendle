"""The Python nav-module emitter — codegen pillar 2 on the emit/ seam.

This is the ON-BRAND codegen: it generates a runnable Python module that runs ON the
wendle framework — `navigate()` helpers + a hooked `replay_recording()` with the
`@hooks.before(n)` / `@hooks.screen(ns)` decorator stubs the developer fills in to inject
custom logic (Frida / AI / code) in the verified gaps and steer with cont()/goto()/stop().

Same DISTINCT contract class as the Maestro emitter: it ships selector values BY DESIGN
(function names / comments are the product), but the hard line holds — NEVER a sensitive
typed value, NEVER raw coordinates (a coords action is flagged, not transcribed).
"""
from wendle import cli
from wendle.emit import get_emitter
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition

PKG = "com.app"


def _graph():
    g = Graph()
    g.upsert_screen(Screen(id="S0", namespace=f"{PKG}/.Login", package=PKG, activity=".Login",
                           force_action=ForceAction("am_start", f"{PKG}/.Login",
                                                    verified_fp="S0")))
    for sid, act in (("S1", ".Home"), ("S2", ".Profile"), ("S3", ".End")):
        g.upsert_screen(Screen(id=sid, namespace=f"{PKG}/{act}", package=PKG, activity=act))
    # a credentialed submit (sensitive pre_action) -> Home
    g.add_transition(Transition(
        source="S0", target="S1",
        action=Action(selector=Selector("label", "Iniciar sesión"), action_type="click"),
        pre_actions=[Action(selector=Selector("resource_id", "com.app:id/pwd"),
                            action_type="set_text", value={"param": "password"},
                            sensitive=True)]))
    # a content-desc tab switch -> Profile (the on-brand global-nav case)
    g.add_transition(Transition(
        source="S1", target="S2",
        action=Action(selector=Selector("content_desc", "Perfil"), action_type="click"),
        global_affordance=True))
    # a coords-only tap -> End: must be flagged, pixels NEVER transcribed
    g.add_transition(Transition(
        source="S2", target="S3",
        action=Action(selector=Selector("coords", (333, 444)), action_type="click",
                      replayability="coordinate_only")))
    return g


def test_registry_has_python_and_declares_codegen_contract():
    e = get_emitter("python")
    assert e.name == "python"
    assert getattr(e, "emits_selector_values", False) is True


def test_generated_module_is_valid_runnable_python():
    out = get_emitter("python").emit(_graph())
    compile(out, "<generated nav module>", "exec")  # parses as real Python or this raises


def test_module_uses_the_framework_api_and_the_hook_decorators():
    out = get_emitter("python").emit(_graph())
    # imports the public verbs + the hook runtime (cont/goto/stop + registry)
    assert "from wendle import Graph, U2Driver, navigate, replay_recording" in out
    assert "from wendle.replay.hooks import HookRegistry, cont, goto, stop" in out
    # a navigate-to-node helper per selector-reachable destination, named from its label
    assert "def go_to_iniciar_sesion(driver" in out
    assert "def go_to_perfil(driver" in out
    assert "navigate(" in out
    # the hook scaffold: an empty registry + commented before/screen stubs to fill in
    assert "hooks = HookRegistry()" in out
    assert "# @hooks.before(" in out and "# @hooks.screen(" in out
    assert "goto(" in out and "stop(" in out and "cont(" in out
    # the hooked replay entry point
    assert "replay_recording(" in out


def test_codegen_hard_lines_no_secrets_no_coordinates():
    out = get_emitter("python").emit(_graph())
    assert "hunter2" not in out          # no sensitive literal exists anywhere
    assert "333" not in out and "444" not in out  # coords tap refused, pixels never emitted
    assert "wendle:refused" in out          # the coords edge is flagged, not silently dropped
    # the credentialed step's typed value never appears; only the {param} handle may
    assert "password" in out  # the param handle name is fine...
    assert "set_text" not in out or "param" in out  # ...but never a typed literal


def test_cli_emits_python_module(tmp_path):
    rec = tmp_path / "rec.json"
    rec.write_text(_graph().to_json())
    out = str(tmp_path / "nav.py")
    assert cli.main(["render", str(rec), "-o", out, "--target", "python"]) == 0
    body = (tmp_path / "nav.py").read_text()
    compile(body, out, "exec")
    assert 'RECORDING = "' + str(rec) + '"' in body   # the real source map path is threaded in
    assert "def go_to_perfil(driver" in body


def test_generated_graph_loader_uses_a_context_manager():
    # review #1/#10: the emitted graph() is called by every go_to_* helper; an unguarded
    # open(RECORDING).read() leaks an FD per call (OSError in an automation loop). Emit a `with`.
    out = get_emitter("python").emit(_graph())
    assert "with open(RECORDING)" in out
    assert "open(RECORDING).read()" not in out   # no bare unclosed open in the template


def test_ident_is_total_for_unrepresentable_values():
    # review #2: _ident must be TOTAL — an emoji/non-ASCII-only value with an empty fallback must
    # not IndexError (a crash escaping emit() violates honesty-first) and must yield a valid id.
    from wendle.emit.python import _ident
    out = _ident("🎉", "")
    assert out and out.isidentifier()
    # and the whole module still compiles when a selector value folds to nothing
    g = Graph()
    g.upsert_screen(Screen(id="H", namespace=f"{PKG}/.M", package=PKG, activity=".M",
                           force_action=ForceAction("am_start", f"{PKG}/.M", verified_fp="H")))
    g.upsert_screen(Screen(id="E", namespace=f"{PKG}/.E", package=PKG, activity=".E"))
    g.add_transition(Transition(source="H", target="E",
                                action=Action(selector=Selector("content_desc", "🎉"),
                                              action_type="click")))
    compile(get_emitter("python").emit(g), "<emoji>", "exec")


def test_emit_takes_recording_path_as_a_parameter_no_singleton_state():
    # review #3: thread the source map path as DATA, not as mutated singleton state — two emits
    # with different paths must not share state, and the default holds when omitted.
    e = get_emitter("python")
    assert 'RECORDING = "alpha.json"' in e.emit(_graph(), recording_path="alpha.json")
    assert 'RECORDING = "beta.json"' in e.emit(_graph(), recording_path="beta.json")
    assert 'RECORDING = "recording.json"' in e.emit(_graph())   # default when not provided
