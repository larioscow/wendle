"""Offline static map render — thin front over the pluggable emitter seam (emit/).

`to_dot`/`render` keep their public signatures (the v1 API); the implementations live in
`wendle.emit` so future codegen targets (Maestro YAML, a Python nav module) plug in
beside the DOT map under the same credential-safety contract."""
from __future__ import annotations

from wendle.emit import get_emitter
from wendle.graph import Graph


def to_dot(graph: Graph) -> str:
    """Return the Graphviz DOT source for `graph` (redaction-safe)."""
    return get_emitter("dot").emit(graph)


def render(graph: Graph, out: str, target: str = "dot", recording_path: str = None) -> str:
    """Emit `graph` as the named `target` ("dot" by default; "flow" = a redaction-safe step
    outline) to `out` and return the path. DOT views with any Graphviz tool, e.g.
    `dot -Tpng <out> -o map.png`. `recording_path` is threaded to self-referencing codegen
    targets (the Python nav module) as the source map they load — DATA, not emitter state."""
    with open(out, "w") as fh:
        fh.write(get_emitter(target).emit(graph, recording_path))
    return out
