"""The honesty contract as a STABLE, IMPORTABLE typed surface — callers branch on constants and a
typed stop reason, not magic strings or substring-matched free-text errors."""
import wendle
from wendle.navigate.navigator import NavOutcome, NavStatus
from wendle.replay.result import ReplayResult, ReplayStatus, ReplayStep, StopReason


def test_nav_status_constants_match_the_outcome_values():
    # the constants are the documented NavOutcome status set; using them avoids hardcoded strings.
    assert NavStatus.ARRIVED == "arrived" and NavStatus.OFF_GRAPH == "off_graph"
    assert NavOutcome("arrived").status == NavStatus.ARRIVED
    assert NavStatus.ARRIVED_UNVERIFIED in NavStatus.all() and "off_graph" in NavStatus.all()
    assert wendle.NavStatus is NavStatus  # exported on the public API


def test_replay_status_constants_and_is_complete():
    assert ReplayStatus.COMPLETED == "completed" and ReplayStatus.STOPPED == "stopped"
    assert ReplayResult(ReplayStatus.COMPLETED).is_complete
    assert not ReplayResult(ReplayStatus.STOPPED).is_complete


def _stopped(error):
    step = ReplayStep(index=1, edge_index=0, kind="action", action_type="click",
                      selector_kind="text", ok=False, error=error)
    return ReplayResult(ReplayStatus.STOPPED, steps=[step], failed_step=step)


def test_stop_reason_classifies_the_failed_step_into_a_typed_taxonomy():
    # the engine's value-free error strings classify to a stable enum + detail — no substring-match.
    assert _stopped("flow_empty:abc123").stop_reason.kind == StopReason.FLOW_EMPTY
    assert _stopped("element not present: text").stop_reason.kind == StopReason.ELEMENT_NOT_PRESENT
    assert _stopped("text did not land").stop_reason.kind == StopReason.TEXT_NOT_LANDED
    assert _stopped("hook_stop:policy_blocked").stop_reason.kind == StopReason.HOOK_STOP
    assert _stopped("goto_no_route:n7").stop_reason.kind == StopReason.GOTO_NO_ROUTE
    # the detail is preserved (value-free), and an unknown string degrades to OTHER, never crashes
    assert _stopped("flow_empty:abc123").stop_reason.detail == "abc123"
    assert _stopped("something novel").stop_reason.kind == StopReason.OTHER
    # a completed replay has no stop reason
    assert ReplayResult(ReplayStatus.COMPLETED).stop_reason is None
