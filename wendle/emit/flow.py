"""The flow-outline emitter — a redaction-safe, human-readable step list of what replay WILL
actually execute. The seam's SECOND implementation: it proves the emitter surface is genuinely
pluggable (and gives `wendle render --target flow` something useful) before any real codegen
(Maestro YAML / Python nav module) is built on the same protocol.

The outline mirrors the ENGINE's own command derivation (`launch_anchor` + `flow_from_recording`)
rather than walking raw transitions, so the step numbers line up with the engine's step indexes
(the `before:<n>` / `after:<n>` hook boundaries) BY CONSTRUCTION — step 0 is the engine's launch,
step N is the N-th command. Scroll-class fork hops and dropped launcher-return gestures never
appear (replay skips them; the reveal rung bridges fork gaps on demand), so the outline can't
show a step that never executes or mis-key a hook.

Output discipline (the credential-safety contract): selector KINDS and {param} handles only —
never a selector value, never a typed value, never raw coordinates (a coords action is
flagged `[coordinate_only]`, matching selector_to_xpath's refusal to give coords an xpath)."""
from __future__ import annotations

from wendle.emit.base import register
from wendle.graph import Graph
from wendle.replay.commands import flow_from_recording, launch_anchor


def _step(action) -> str:
    bits = [action.action_type, action.selector.kind]
    param = (action.value or {}).get("param")
    if param:
        bits.append("{param:" + str(param) + "}")
    line = " ".join(bits)
    if action.selector.kind == "coords":
        line += " [coordinate_only]"  # content-relative pixels are never transcribed
    return line


class FlowEmitter:
    name = "flow"

    def emit(self, graph: Graph, recording_path=None) -> str:
        anchor = launch_anchor(graph)
        start_id = anchor.verified_fp if anchor is not None else None
        cmds = flow_from_recording(graph, start_id=start_id)
        lines = [f"# nav flow — {graph.g.number_of_nodes()} screen(s), "
                 f"{len(cmds)} replay step(s) after launch; step numbers = engine/hook "
                 f"indexes; redaction-safe: kinds + {{param}} handles only"]
        if anchor is not None:
            start = graph.screen(start_id)
            ns = start.namespace if start is not None else ""
            lines.append(f"0. launch {ns} ({anchor.kind} anchor)")
        for i, cmd in enumerate(cmds, start=1):
            if cmd.kind == "launch":
                lines.append(f"{i}. launch ({cmd.anchor.kind} anchor)")
                continue
            tag = " [pre]" if cmd.kind == "pre" else ""
            lines.append(f"{i}. {_step(cmd.action)}{tag}")
        return "\n".join(lines) + "\n"


register(FlowEmitter())
