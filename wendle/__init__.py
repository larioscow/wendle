"""wendle — record → replay → navigate ANY Android app, semantic-first and honesty-first.

The v1 public API is three verbs over one map:
    record(driver=None, ...)        -> Graph     walk an app by hand, get a navigable map
    replay(path_or_graph, driver)   -> ReplayResult  faithfully re-enact a recording on the device
    navigate(graph, from, to, driver) -> NavOutcome  route to any node, verifying arrival

The cardinal contract is HONESTY: replay/navigate NEVER confidently do the wrong thing — on any
ambiguity they STOP AND REPORT (a typed status naming the step + element) rather than a
plausible-but-wrong success. `ReplayResult`/`NavOutcome` carry those typed outcomes; their repr is
redaction-safe (never a selector value or typed text).

    import wendle
    graph = wendle.record(duration=90, out="myapp.json")    # connects + calibrates a real device
    wendle.replay("myapp.json", wendle.U2Driver())             # reproduce the walk
    wendle.navigate(graph, graph.anchors()[0], target_id, wendle.U2Driver())

Drivers: `U2Driver(serial)` drives a real device (uiautomator2 is imported lazily, only when you
instantiate it); `FakeDriver` is the in-memory seam for device-free tests.

Out of scope for v1 (planned v2): autonomous crawling (exploring an app to build the map without a
human walk) and codegen (emitting a reusable navigation module / Maestro flows from the map).
"""
from wendle.driver.fake import FakeDriver
from wendle.driver.u2_driver import U2Driver
from wendle.graph import Graph, StaleRecordingError
from wendle.navigate.navigator import NavOutcome, NavStatus, Navigator, navigate
from wendle.record import record
from wendle.render import render, to_dot
from wendle.replay.engine import ReplayEngine, replay_recording
from wendle.replay.result import (
    ReplayResult,
    ReplayStatus,
    ReplayStep,
    StopInfo,
    StopReason,
)

# friendly alias: `wendle.replay(...)` reads better than `replay_recording`
replay = replay_recording

__version__ = "0.1.0"

__all__ = [
    # the three verbs
    "record",
    "replay",
    "replay_recording",
    "navigate",
    "render",
    "to_dot",
    # the map + honesty result types (the stable contract callers program against)
    "Graph",
    "NavOutcome",
    "NavStatus",
    "ReplayResult",
    "ReplayStatus",
    "ReplayStep",
    "StopReason",
    "StopInfo",
    "StaleRecordingError",
    # engines (for advanced use: hooks, custom config)
    "ReplayEngine",
    "Navigator",
    # device seam
    "U2Driver",
    "FakeDriver",
    "__version__",
]
