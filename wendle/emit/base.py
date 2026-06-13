"""The graph→emitter seam (v2 codegen prep).

An `Emitter` turns a recorded Graph into a text artifact (DOT map, flow outline — later:
Maestro YAML, a Python nav module). The seam exists BEFORE any real codegen because codegen
is the worst credential-leak sink: every emitter, present and future, is bound by the
CREDENTIAL-SAFETY CONTRACT (enforced by a registry-wide test):

  * never a selector VALUE (PII even when not flagged sensitive — same rule as the
    redaction-safe result reprs),
  * never a typed value — a sensitive field appears only as its {param} handle,
  * never raw coordinates (a coords action is flagged, not transcribed).

New emitters plug in via `register()`; `get_emitter(target)` refuses unknown targets typed
(never a silent fallback)."""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from wendle.graph import Graph

# The selector kinds that resolve to a RUNNABLE, stable handle (a value an emitter can transcribe)
# — the one source of truth shared by every codegen emitter, so a new kind is added in ONE place
# and the emitters cannot silently diverge on what they accept. 'coords' is deliberately absent
# (pixels are never transcribed); 'keyevent'/'xpath' are not capture-produced selector kinds.
NAMEABLE_SELECTOR_KINDS = frozenset({"label", "text", "content_desc", "resource_id", "hint"})


class Emitter(Protocol):
    name: str

    def emit(self, graph: Graph, recording_path: Optional[str] = None) -> str:  # pragma: no cover
        # recording_path: the source map a self-referencing codegen module should load (the Python
        # nav module). Optional and ignored by value-free emitters (DOT/flow) — threaded as DATA so
        # no emitter holds per-call state. Protocol signature only.
        ...


_REGISTRY: Dict[str, "Emitter"] = {}


def register(emitter: "Emitter") -> "Emitter":
    _REGISTRY[emitter.name] = emitter
    return emitter


def get_emitter(target: str) -> "Emitter":
    try:
        return _REGISTRY[target]
    except KeyError:
        raise ValueError(
            f"unknown emit target {target!r} (available: {', '.join(sorted(_REGISTRY))})")


def all_emitters() -> List["Emitter"]:
    """Every registered emitter — the credential-safety contract test iterates THIS, so a
    future emitter cannot ship outside the contract."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
