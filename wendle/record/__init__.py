"""Recorder: walk an app by hand -> a navigable Graph (screens + transitions)."""
from wendle.record.loop import record
from wendle.record.session import RecordSession

__all__ = ["record", "RecordSession"]
