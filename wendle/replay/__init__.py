"""Faithful record→replay engine (Maestro-cloned).

Re-enacts a recording on the device as one linear command list: per command it WAITS for the
target element (polling, never a blind sleep), acts, verifies the effect, settles, and STOPS
HONESTLY (naming the step + element) when something never appears. The map/navigator is a
separate concern; this just reproduces what was captured.
"""
from wendle.replay.commands import Command, flow_from_recording, launch_anchor
from wendle.replay.engine import ReplayEngine, replay_recording
from wendle.replay.hooks import (
    Hook,
    HookContext,
    HookRegistry,
    HookResult,
    cont,
    goto,
    stop,
)
from wendle.replay.result import ReplayResult, ReplayStep

__all__ = [
    "ReplayEngine",
    "replay_recording",
    "ReplayResult",
    "ReplayStep",
    "Command",
    "flow_from_recording",
    "launch_anchor",
    # inter-step injection hooks — the developer extension point
    "Hook",
    "HookContext",
    "HookResult",
    "cont",
    "goto",
    "stop",
]
