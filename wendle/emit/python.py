"""The Python nav-module emitter — the ON-BRAND codegen target.

Where the Maestro emitter exports the flow to a competing runner, THIS emitter generates a
runnable Python module that runs ON wendle itself — so the honesty gates and THE
defining feature (inter-step hooks) come along. The generated module gives the consumer:

  * `go_to_<label>(driver)` helpers — each pathfinds to a VERIFIED node via `navigate()` and
    returns a NavOutcome (status == "arrived" is a real verified landing; anything else is a
    typed HONEST stop, never a wrong screen);
  * a hooked `replay()` over the recorded flow with the `@hooks.before(n)` / `@hooks.after(n)`
    / `@hooks.screen(ns)` decorator STUBS the developer fills in to inject custom logic — a
    Frida script, an AI agent, arbitrary code — into the verified gaps and steer the run with
    cont() / goto(node) / stop(reason).

The hook step indexes are the ENGINE's own: 0-based over `flow_from_recording` (the launch is a
separate boundary; `before:0` fires before the first recorded command), so a generated
`@hooks.before(i)` lands on exactly the boundary the comment names.

CODEGEN CONTRACT CLASS (`emits_selector_values=True`): selector values ARE the product (they
become function names and reference comments), but the hard lines hold for every emitter:
  * a sensitive field NEVER appears as a literal — only its `{param:<handle>}` name;
  * raw coordinates are NEVER transcribed — a coordinate-only destination/step is flagged with
    an explicit `# wendle:refused` comment, never pixels, never a silent drop.
The output always parses as valid Python (a malformed scaffold would defeat the point)."""
from __future__ import annotations

import re
import unicodedata
from typing import Set

from wendle.emit.base import NAMEABLE_SELECTOR_KINDS, register
from wendle.graph import Graph
from wendle.replay.commands import flow_from_recording, launch_anchor


def _ident(value, fallback: str) -> str:
    """A safe lower_snake Python identifier from a selector value (accent-folded, alnum-run
    collapsed). TOTAL: when both the value AND `fallback` fold to nothing (e.g. an emoji-only
    label), degrade to 'node' so the result is always a non-empty valid identifier — an
    IndexError escaping emit() would be a honesty-first crash. A leading digit is prefixed."""
    folded = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"_+", "_", re.sub(r"[^0-9a-zA-Z]+", "_", folded)).strip("_").lower()
    if not s:
        s = re.sub(r"[^0-9a-zA-Z_]+", "_", str(fallback)).strip("_").lower() or "node"
    if s[0].isdigit():
        s = "_" + s
    return s


def _safe(value) -> str:
    """One-line, comment-safe rendering of a selector value (never breaks out of a `#` line)."""
    return str(value).replace("\\", "\\\\").replace("\n", " ").replace("\r", " ")


def _q(value) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _step_caption(cmd) -> str:
    """A redaction-safe one-line description of a flow command (kind + selector kind + safe
    label / {param} handle), NEVER a typed value, NEVER coordinates."""
    if cmd.kind == "launch":
        return f"launch ({cmd.anchor.kind} anchor)"
    a = cmd.action
    if a.selector.kind == "coords":
        return f"{a.action_type} [coordinate_only]"
    param = (a.value or {}).get("param")
    tag = "[pre] " if cmd.kind == "pre" else ""
    handle = f' {{param:{_safe(param)}}}' if param else f' -> "{_safe(a.selector.value)}"'
    return f"{tag}{a.action_type} {a.selector.kind}{handle}"


class PythonEmitter:
    name = "python"
    emits_selector_values = True  # codegen class: values are the product (hard lines still hold)

    def emit(self, graph: Graph, recording_path=None) -> str:
        # recording_path is threaded as DATA (never held on the instance) so concurrent/repeat
        # emits never share state. NOTE: it is transcribed verbatim as the RECORDING literal —
        # it is a user-chosen FILE PATH, so callers must not embed a secret in the path itself
        # (outside the framework's recorded-value redaction surface).
        rec_path = recording_path or "recording.json"
        anchor = launch_anchor(graph)
        top_id = anchor.verified_fp if anchor is not None else None
        start = graph.screen(top_id) if top_id else None
        pkg = start.package if start is not None and start.package else ""
        flow = flow_from_recording(graph, start_id=top_id)
        return "\n".join(self._header(rec_path, pkg, top_id)
                         + self._nav_helpers(graph, top_id)
                         + self._hook_scaffold(graph, flow)
                         + self._footer())

    @staticmethod
    def _header(rec_path, pkg, top_id) -> list:
        """Module docstring, framework imports, and the RECORDING/PKG/TOP constants + graph()."""
        return [
            f'"""Reusable navigation module for {pkg or "the recorded app"} — generated by',
            "wendle (wendle render --target python). Runs ON the framework: verified",
            "navigation + hooked replay. Fill in the @hooks stubs to inject your own logic",
            "(Frida / AI / code) in the verified gaps and steer with cont() / goto() / stop().",
            '"""',
            "from __future__ import annotations",
            "",
            "from wendle import Graph, U2Driver, navigate, replay_recording",
            "from wendle.replay.hooks import HookRegistry, cont, goto, stop",
            "",
            f"RECORDING = {_q(rec_path)}  # the verified map this was generated from",
            f"PKG = {_q(pkg)}",
            f"TOP = {_q(top_id) if top_id else 'None'}  # launch anchor — cold-launch start node",
            "",
            "",
            "def graph() -> Graph:",
            "    with open(RECORDING) as fh:  # context-managed: a long automation loop won't leak FDs",
            "        return Graph.from_json(fh.read())",
        ]

    @staticmethod
    def _nav_helpers(graph, top_id) -> list:
        """A go_to_<label>(driver) per selector-reachable destination; a node reachable ONLY by a
        coordinate edge is flagged # wendle:refused (never a pixel-bound helper)."""
        out = [
            "",
            "",
            "# == navigate to any mapped screen (verified arrival; honest stop, never wrong) ==",
        ]
        if top_id is None:
            out.append("# wendle:refused — no launch anchor recorded; cold-launch navigation needs one.")
        used: Set[str] = set()
        seen_targets: Set[str] = set()
        refused: list = []
        for _u, v, _k, d in graph.ordered_transitions():
            a = d["action"]
            if v == top_id or v in seen_targets:
                continue
            if a.selector.kind not in NAMEABLE_SELECTOR_KINDS:
                if v not in refused:
                    refused.append(v)
                continue
            seen_targets.add(v)
            name = _ident(a.selector.value, f"node_{_ident(v, 'x')}")
            while name in used:
                name += "_2"
            used.add(name)
            out += [
                "",
                f"def go_to_{name}(driver):  # -> {_safe(a.selector.value)!r} ({a.selector.kind})",
                "    g = graph()",
                f"    return navigate(g, TOP, {_q(v)}, driver)",
            ]
        for v in refused:
            if v not in seen_targets:  # only flag targets with NO nameable route at all
                out.append(f"# wendle:refused go_to for node {_q(v)}: only a coordinate-only edge "
                           "reaches it — route to it from a verified neighbor via a goto() hook.")
        return out

    @staticmethod
    def _hook_scaffold(graph, flow) -> list:
        """The empty HookRegistry + commented before/after/screen stubs (0-based engine step
        indexes) the developer fills in to steer the run with cont()/goto()/stop()."""
        out = [
            "",
            "",
            "# == hooked replay: inject your logic in the verified gaps between steps ==",
            "# The recorded flow is the skeleton; the hooks are the brain. Uncomment a stub and",
            "# return cont() (proceed) / goto(\"<node>\") (re-route: pathfind + verified arrival)",
            "# / stop(\"reason\") (honest halt). Step indexes are the recording's own (0-based):",
        ]
        for i, cmd in enumerate(flow):
            out.append(f"#   step {i}: {_step_caption(cmd)}")
        last = len(flow) - 1
        dest_ns = []
        for _u, v, _k, _d in graph.ordered_transitions():
            s = graph.screen(v)
            if s is not None and s.namespace and s.namespace not in dest_ns:
                dest_ns.append(s.namespace)
        out += [
            "",
            "hooks = HookRegistry()",
            "",
            "# --- fire before the first recorded step (e.g. skip ahead to a deep section) ---",
            "# @hooks.before(0)",
            "# def before_first(ctx):",
            "#     return cont()  # or: return goto(\"<node>\")",
        ]
        if last >= 0:
            out += [
                "",
                f"# --- fire right after the recorded step {last} verifies (branch on live state) ---",
                f"# @hooks.after({last})",
                "# def after_last(ctx):",
                "#     decision = my_agent.decide(ctx)   # arbitrary code in the verified gap",
                "#     if decision == \"a\": return goto(\"<node_x>\")",
                "#     if decision == \"b\": return goto(\"<node_y>\")",
                "#     return stop(\"undecided\")          # else: honest stop",
            ]
        for ns in dest_ns[:3]:
            hook_name = _ident(ns.split("/")[-1], "screen")
            out += [
                "",
                f"# --- fire at every verified arrival on {ns} ---",
                f"# @hooks.screen({_q(ns)})",
                f"# def on_{hook_name}(ctx):",
                "#     ctx.emit(\"fact\", read_via_frida(ctx))  # values return in result.data",
                "#     return cont()",
            ]
        return out

    @staticmethod
    def _footer() -> list:
        """The hooked replay() entry point + __main__ guard."""
        return [
            "",
            "",
            "def replay(driver=None):",
            '    """Replay the recorded flow with your hooks running in the verified gaps."""',
            "    return replay_recording(RECORDING, driver or U2Driver(), hooks=hooks)",
            "",
            "",
            'if __name__ == "__main__":',
            "    print(replay())",
            "",
        ]


register(PythonEmitter())
