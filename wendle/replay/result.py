"""Structured, REDACTION-SAFE replay outcomes.

`__repr__` NEVER emits a selector VALUE or any typed value — in real recordings a field's
selector/value can be the literal you typed (card number, date), which is PII even when the
recorder didn't flag it sensitive. We print only the selector KIND and the step coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class ReplayStatus(str):
    """ReplayResult.status — the two terminal outcomes, as importable constants."""

    COMPLETED = "completed"   # the whole recording re-enacted faithfully
    STOPPED = "stopped"       # halted HONESTLY (a confident-wrong success is never produced)


class StopReason(str):
    """The stable taxonomy of WHY a replay stopped — so callers branch on a typed kind instead of
    substring-matching the engine's (value-free) error strings. `ReplayResult.stop_reason` parses
    the failed step's error into one of these + a value-free `detail`."""

    ELEMENT_NOT_PRESENT = "element_not_present"   # the target element never appeared (wait timeout)
    NOT_RESOLVED = "not_resolved"                  # the recorded selector did not resolve
    AMBIGUOUS_MATCH = "ambiguous_match"            # a label union matched >1 distinct element — refused
    TEXT_NOT_LANDED = "text_not_landed"            # a set_text was issued but the value did not land
    CHECKBOX_DRIFT = "checkbox_drift"              # a set_checked could not reach the target state
    COORDINATE_REFUSED = "coordinate_refused"      # a raw-coordinate action refused (refusal on)
    CREDENTIAL_REQUIRED = "credential_required"    # a sensitive field needs a param not supplied
    UNSUPPORTED = "unsupported"                    # a malformed/decayed action could not be replayed
    FLOW_EMPTY = "flow_empty"                       # the anchor screen has no recorded outbound flow
    RESUME_EMPTY = "resume_empty"                   # a goto target is a true leaf (no continuation)
    RESUME_OFF_FLOW = "resume_off_flow"             # a goto target departs only into a dropped region
    GOTO_NO_ROUTE = "goto_no_route"                 # a goto target is not in the graph
    GOTO_FAILED = "goto_failed"                     # a goto's inner navigate() refused (typed nav status)
    GOTO_BUDGET = "goto_budget"                     # the per-run goto budget was exhausted
    HOOK_STOP = "hook_stop"                         # a developer hook returned stop()
    HOOK_FAILED = "hook_failed"                     # a developer hook raised
    # §3 reveal rung (lazy-region design) — additive members, all observation-only (L4):
    REVEAL_NO_CONTAINER = "reveal_no_container"     # no recorded evidence binds a container
    REVEAL_AMBIGUOUS = "reveal_ambiguous"           # >=2 in-container matches; refused
    REVEAL_NO_MOVEMENT = "reveal_no_movement"       # a step produced no observable region change
    REVEAL_BUDGET = "reveal_budget"                 # step/wall cap (endless feeds end here)
    OTHER = "other"                                 # an unclassified (still value-free) reason

    # error-string prefix -> typed kind (the engine's strings are stable + value-free). Order
    # matters only in that colon-suffixed prefixes carry a detail; matching is by startswith.
    _PREFIXES = (
        ("element not present", ELEMENT_NOT_PRESENT),
        ("ambiguous match", AMBIGUOUS_MATCH),
        ("text did not land", TEXT_NOT_LANDED),
        ("did not resolve", NOT_RESOLVED),
        ("checkbox", CHECKBOX_DRIFT),
        ("coordinate", COORDINATE_REFUSED),
        ("credential", CREDENTIAL_REQUIRED),
        ("unsupported", UNSUPPORTED),
        ("flow_empty:", FLOW_EMPTY),
        ("resume_empty:", RESUME_EMPTY),
        ("resume_off_flow:", RESUME_OFF_FLOW),
        ("goto_no_route:", GOTO_NO_ROUTE),
        ("goto_failed:", GOTO_FAILED),
        ("goto_budget", GOTO_BUDGET),
        ("hook_stop:", HOOK_STOP),
        ("hook_failed:", HOOK_FAILED),
        ("reveal_no_container:", REVEAL_NO_CONTAINER),
        ("reveal_ambiguous:", REVEAL_AMBIGUOUS),
        ("reveal_no_movement:", REVEAL_NO_MOVEMENT),
        ("reveal_budget:", REVEAL_BUDGET),
    )


@dataclass
class StopInfo:
    """A typed, value-free replay stop: WHY it halted plus an optional detail (a node id / reason
    token the engine already prints — never a selector value or typed secret)."""

    kind: str
    detail: str = ""

    def __repr__(self) -> str:
        return f"<stop {self.kind}{(' ' + self.detail) if self.detail else ''}>"


def classify_stop(error: Optional[str]) -> Optional["StopInfo"]:
    """Map a value-free engine error string to a typed StopInfo, or None if there is no error."""
    if not error:
        return None
    low = error.lower()
    for prefix, kind in StopReason._PREFIXES:
        if low.startswith(prefix):
            detail = error.split(":", 1)[1].strip() if (prefix.endswith(":") and ":" in error) else ""
            return StopInfo(kind=kind, detail=detail)
    return StopInfo(kind=StopReason.OTHER, detail="")


@dataclass
class ReplayStep:
    index: int          # position in the whole command list
    edge_index: int     # which recorded transition this came from
    kind: str           # 'launch' | 'pre' | 'action'
    action_type: str    # click | long_click | swipe | set_text | set_checked | keyevent | launch
    selector_kind: str  # text | content_desc | resource_id | coords | keyevent | '-'
    ok: bool
    error: Optional[str] = None  # value-free strings only (e.g. 'element not present: text')
    settled: bool = True
    low_confidence: bool = False  # e.g. a reproduced coordinate tap (no stable element)

    def __repr__(self) -> str:
        st = "ok" if self.ok else f"FAIL[{self.error}]"
        lc = " ~lowconf" if self.low_confidence else ""
        return (f"<step {self.index} e{self.edge_index} {self.kind} "
                f"{self.action_type}:{self.selector_kind} {st}{lc}>")


@dataclass
class ReplayResult:
    status: str  # 'completed' | 'stopped'
    steps: List[ReplayStep] = field(default_factory=list)
    failed_step: Optional[ReplayStep] = None
    data: dict = field(default_factory=dict)  # values a hook surfaced via ctx.emit (VALUE-FREE)

    @property
    def ok(self) -> bool:
        return self.status == ReplayStatus.COMPLETED

    @property
    def is_complete(self) -> bool:
        return self.status == ReplayStatus.COMPLETED

    @property
    def stop_reason(self) -> Optional["StopInfo"]:
        """The TYPED reason this replay stopped (kind + value-free detail), or None if it completed —
        so a caller branches on `result.stop_reason.kind == StopReason.ELEMENT_NOT_PRESENT` instead
        of substring-matching free text."""
        if self.status == ReplayStatus.COMPLETED or self.failed_step is None:
            return None
        return classify_stop(self.failed_step.error)

    def __repr__(self) -> str:
        tail = f" failed={self.failed_step!r}" if self.failed_step else ""
        return f"<ReplayResult {self.status} {len(self.steps)} step(s){tail}>"
