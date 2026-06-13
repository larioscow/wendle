"""The Graphviz DOT emitter — the seam's reference implementation (moved from render.py).

Dependency-light (pure text — no graphviz binary needed; `dot -Tpng map.dot -o map.png`
renders it) and REDACTION-SAFE: node/edge labels carry only the screen NAMESPACE / type and
the action+selector KIND — NEVER a selector VALUE or typed text, which can be PII even when
not flagged sensitive (same rule as ReplayResult/NavOutcome repr)."""
from __future__ import annotations

from wendle.emit.base import register
from wendle.graph import Graph


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _node_label(screen) -> str:
    bits = [screen.namespace]
    tags = []
    if screen.force_action is not None:
        tags.append(f"anchor:{screen.force_action.kind}")  # am_start / keyevent launch anchor
    if screen.coarse_id is not None:
        tags.append("refined-twin")                        # task #17b refined sibling node
    if screen.volatile:
        tags.append("volatile")
    if screen.adapter_dominant:
        tags.append("list")
    if tags:
        bits.append("[" + ", ".join(tags) + "]")
    return "\\n".join(_esc(b) for b in bits)


class DotEmitter:
    name = "dot"

    def emit(self, graph: Graph, recording_path=None) -> str:
        lines = ["digraph nav_map {", "  rankdir=LR;", '  node [shape=box, fontsize=10];']
        for nid in graph.g.nodes:
            s = graph.screen(nid)
            if s is None:
                continue
            shape = "doubleoctagon" if (s.force_action is not None) else "box"
            style = ', style="dashed"' if s.volatile else ""
            lines.append(f'  "{_esc(nid)}" [label="{_node_label(s)}", shape={shape}{style}];')
        for u, v, _key, data in graph.ordered_transitions():
            a = data.get("action")
            # edge label = action_type:selector_KIND only — NEVER the selector value (redaction).
            lbl = f"{a.action_type}:{a.selector.kind}" if a is not None else "?"
            lines.append(f'  "{_esc(u)}" -> "{_esc(v)}" [label="{_esc(lbl)}", fontsize=8];')
        lines.append("}")
        return "\n".join(lines) + "\n"


register(DotEmitter())
