"""One shared action executor — the single place that turns a recorded Action into a driver
operation. The replay engine and the navigator both route every action through it, so a new
gesture is one ~8-line handler + one test, available to both, and the two can never drift again.

This closes a real honesty bug: the navigator used to have NO swipe and NO keyevent branch, so a
routed swipe edge tapped the element's CENTER (a confident-wrong action) and a keyevent edge
crashed. Both subsystems now get every handler.

Caller policy differences are CONTEXT FLAGS, not forked code:
  - `reproduce_coords`  replay reproduces a coordinate tap (flagged low-confidence); the navigator
                        refuses it (a coordinate-only tap is a fragile, element-less bind).
  - `faithful_text`     replay re-enacts typing through the keyboard (focus + clear + IME); the
                        navigator uses the faster atomic/`per_key` path.
  - `verify_text`       replay confirms the typed value landed; the navigator does not. Its closed
                        loop re-observes SCREEN ARRIVAL each step (not field contents), so a silent
                        text no-op surfaces LATER as an honest content_drift/off_graph STOP — never
                        a confident wrong arrival, but also not a per-step text-landing guarantee.
                        (Making the navigator verify is a tracked follow-up, not part of this refactor.)
The result is TYPED (`reason`) so callers map control flow without substring-matching error text.
Error strings stay VALUE-FREE (never a typed value or secret).

NOTE — this extraction is behavior-preserving for replay, but makes the navigator slightly MORE
honest in two cases (both verified by review): a pre_action that fails now STOPS the submit edge
(the old loop checked only the error string and swallowed an `(ok=False, error=None)` pre_action),
and a vanished `set_checked` now reports content_drift instead of a mislabeled coordinate refusal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

# typed outcomes
OK = "ok"
NOT_RESOLVED = "not_resolved"            # the recorded element didn't resolve (content drift)
CREDENTIAL_REQUIRED = "credential_required"
COORDINATE_REFUSED = "coordinate_refused"
TEXT_NOT_LANDED = "text_not_landed"
CHECKBOX_DRIFT = "checkbox_drift"
CHECKBOX_VANISHED = "checkbox_vanished"  # widget gone AFTER the tap — flip unverifiable
AMBIGUOUS_MATCH = "ambiguous_match"      # selector matched >1 distinct element — refused, never first-match
UNSUPPORTED = "unsupported"              # action cannot run on this selector kind


@dataclass
class ActionResult:
    ok: bool
    reason: str = OK
    error: Optional[str] = None          # value-free message (never a typed value / secret)
    low_confidence: bool = False


def _action_mode(action) -> str:
    return (action.value or {}).get("replay_mode", "atomic")


@dataclass
class ActionContext:
    driver: object
    params: Dict[str, str] = field(default_factory=dict)
    clock: Optional[Callable] = None
    sleep: Optional[Callable] = None
    verify_timeout: float = 3.0
    reproduce_coords: bool = True          # replay reproduces a coords tap; navigator refuses
    faithful_text: bool = True             # replay re-enacts via the keyboard; navigator atomic/per_key
    verify_text: bool = True               # confirm typed text landed
    resolve_mode: Callable = _action_mode  # action -> 'atomic' | 'per_key' (navigator override hook)


def _text_value(action, ctx: ActionContext):
    """(value, None) or (None, ActionResult) when a credential is required but absent. The secret
    is read from params by NAME and never echoed into an error string."""
    if action.sensitive:
        name = (action.value or {}).get("param")
        if name not in ctx.params:
            return None, ActionResult(False, CREDENTIAL_REQUIRED, f"credential required: {name}")
        return ctx.params[name], None
    return (action.value or {}).get("text", ""), None


def _set_text(action, ctx: ActionContext) -> ActionResult:
    sel = action.selector
    if sel.kind == "coords":
        return ActionResult(False, UNSUPPORTED, "set_text on coordinate-only selector")
    value, err = _text_value(action, ctx)
    if err is not None:
        return err
    if ctx.faithful_text:
        landed = ctx.driver.focus_and_type(sel, value)  # focus + clear + IME (fires TextWatchers)
    else:
        if ctx.resolve_mode(action) == "per_key" and ctx.driver.supports("type_text"):
            ctx.driver.type_text(sel, value)             # reactive field (search-as-you-type)
        else:
            ctx.driver.set_text(sel, value)              # atomic — fast, deterministic
        landed = True
    if not landed:
        return ActionResult(False, NOT_RESOLVED, "field not present to type into")
    if ctx.verify_text and ctx.driver.supports("verify_text"):
        if not ctx.driver.verify_text(sel, value, masked=bool(action.sensitive),
                                      timeout=ctx.verify_timeout, clock=ctx.clock, sleep=ctx.sleep):
            return ActionResult(False, TEXT_NOT_LANDED, "text did not land")  # value-free
    return ActionResult(True)


def _set_checked(action, ctx: ActionContext) -> ActionResult:
    sel = action.selector
    if sel.kind == "coords":
        return ActionResult(False, UNSUPPORTED, "set_checked on coordinate-only selector")
    try:
        ok = ctx.driver.set_checked(sel, bool((action.value or {}).get("checked")),
                                    clock=ctx.clock, sleep=ctx.sleep)
    except Exception:  # noqa: BLE001 — element vanished (drift)
        return ActionResult(False, CHECKBOX_DRIFT, "set_checked target not resolvable")
    if ok is None:
        # tapped, then the widget VANISHED (self-dismissing gate): the flip is UNVERIFIABLE —
        # never claim success unverified, and never report drift ("did not flip") for a tap
        # that may have worked. Typed, so callers stop honestly naming the divergence.
        return ActionResult(False, CHECKBOX_VANISHED, "checkable vanished after tap")
    return ActionResult(ok, OK if ok else CHECKBOX_DRIFT,
                        None if ok else "checkbox did not reach target")


def _swipe(action, ctx: ActionContext) -> ActionResult:
    sel = action.selector
    if sel.kind == "coords" and getattr(action, "in_region", False):
        # §4 (lazy-region design): a coordinate recorded inside an adapter region is
        # meaningless once content moves — replaying it would swipe a DIFFERENT row
        # (a confident-wrong state mutation). Unconditional typed refusal.
        return ActionResult(False, COORDINATE_REFUSED,
                            "coordinate refused: recorded inside an adapter region")
    end = tuple(action.end) if action.end else None
    # coords selector -> start is the recorded point; a semantically-anchored swipe (older
    # recordings stored the start element's label) -> reconstruct start from its center.
    start = tuple(sel.value) if sel.kind == "coords" else ctx.driver.element_center(sel)
    if start is None or end is None:
        return ActionResult(False, NOT_RESOLVED, "swipe missing start/end")
    ctx.driver.swipe(start, end)
    return ActionResult(True)


def _keyevent(action, ctx: ActionContext) -> ActionResult:
    sel = action.selector
    code = sel.value if sel.kind == "keyevent" else (action.value or {}).get("code")
    try:
        code = int(code)
    except (TypeError, ValueError):  # malformed/decayed edge: no code anywhere -> typed, not a crash
        return ActionResult(False, UNSUPPORTED, "keyevent without a key code")
    ctx.driver.keyevent(code)
    return ActionResult(True)


def _tap(action, ctx: ActionContext) -> ActionResult:
    sel, at = action.selector, action.action_type
    if sel.kind == "coords":
        if getattr(action, "in_region", False):
            # §4: in-region coordinates refuse UNCONDITIONALLY — replay's reproduce_coords
            # policy does not apply (the content under the pixel has moved by construction;
            # knowing-and-tapping-anyway is the cardinal-sin path).
            return ActionResult(False, COORDINATE_REFUSED,
                                "coordinate refused: recorded inside an adapter region")
        if not ctx.reproduce_coords:
            return ActionResult(False, COORDINATE_REFUSED, "coordinate_only edge refused")
        ctx.driver.resolve_and_tap(sel, at)  # reproduce the recorded spot — no stable element
        return ActionResult(True, low_confidence=True)
    ok = ctx.driver.resolve_and_tap(sel, at)
    if ok is None:
        # tri-state (the set_checked precedent): the selector matched MORE THAN ONE distinct
        # element (a label-union lookalike) — the driver refused rather than tap the first.
        # No decay retry: the elements ARE present; a contains-retry would only re-find the
        # ambiguity (or a third lookalike). Typed + value-free.
        return ActionResult(False, AMBIGUOUS_MATCH, f"ambiguous match: {sel.kind}")
    if not ok and sel.kind in ("text", "content_desc", "label"):
        # the recorded label may have decayed ('Name, sent 53 min ago' -> the timestamp moved);
        # retry on the STABLE leading segment (before the first comma). 'label' is the §4 union
        # kind every fresh text anchor now carries — it must keep this recovery, not lose it.
        stable = str(sel.value).split(",")[0].strip()
        if stable and stable != sel.value:
            ok = ctx.driver.tap_contains(sel.kind, stable, at)
    return ActionResult(ok, OK if ok else NOT_RESOLVED,
                        None if ok else "recorded element did not resolve")


# action_type -> handler. New gestures plug in here as one unit; the default is a tap
# (click / long_click / anything not specially handled).
_HANDLERS = {
    "set_text": _set_text,
    "set_checked": _set_checked,
    "swipe": _swipe,
    "keyevent": _keyevent,
}


def execute(action, ctx: ActionContext) -> ActionResult:
    """Run one recorded action against the driver, per the caller's context policy."""
    return _HANDLERS.get(action.action_type, _tap)(action, ctx)
