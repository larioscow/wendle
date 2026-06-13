"""The library-level `record()` entry: walk an app by hand, get back a navigable Graph.

Orchestrates calibration + a RecordSession + the live `getevent` gesture stream into one call, so a
consumer never has to copy the spike glue. The device `getevent` stream can be replaced by an
injected `gestures` iterable — the same Gesture seam the recorder unit tests drive — so the loop is
device-free testable and a captured gesture log can be re-fed deterministically.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from wendle.capture.types import Gesture
from wendle.graph import Graph
from wendle.models import DeviceProfile
from wendle.record.session import RecordSession


def record(
    driver=None,
    *,
    duration: float = 120.0,
    out: Optional[str] = None,
    profile: Optional[DeviceProfile] = None,
    serial: Optional[str] = None,
    live_refresh: bool = True,
    settle_kwargs: Optional[dict] = None,
    sink: Optional[Callable[[dict], None]] = None,
    on_transition: Optional[Callable] = None,
    stop_event=None,
    gestures: Optional[Iterable[Gesture]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Graph:
    """Record a navigable Graph by walking an app by hand. Returns the Graph (saving to `out` too
    when given). Pass nothing for `driver` to connect a U2Driver(serial) and `calibrate` it.

    Walk the app deliberately, pausing on each screen so it settles; each tap is captured, each
    settled screen fingerprinted, and Screens + Transitions accumulate. `on_transition(t)` fires per
    recorded edge (live progress). The loop runs until `duration` elapses or `stop_event` is set.

    `gestures` (an iterable of Gesture) replaces the live `getevent` stream — for device-free tests
    and re-feeding a captured log; `profile` is then required (no device to calibrate against).
    """
    if driver is None:
        from wendle.driver.u2_driver import U2Driver  # lazy: no device needed to import
        driver = U2Driver(serial)
    if profile is None:
        from wendle.calibration.calibrate import calibrate
        profile = calibrate(driver)

    settle_kwargs = settle_kwargs if settle_kwargs is not None else {"max_wait": 1.6}
    session = RecordSession(driver, profile, sink=sink or (lambda _e: None),
                            live_refresh=live_refresh, settle_kwargs=settle_kwargs)
    session.start()
    try:
        if gestures is not None:
            _feed(session, gestures, on_transition)
        else:
            _stream_from_device(session, driver, profile, duration, stop_event, on_transition, clock)
    finally:
        session.stop()
        if out:
            session.graph.save(out)
    return session.graph


def _feed(session, gestures, on_transition) -> None:
    for g in gestures:
        if g.kind == "multi":
            continue  # multi-finger not modeled in v1 — skip, never mis-record
        t = session.record_gesture(g)
        if t is not None and on_transition is not None:
            on_transition(t)


def _stream_from_device(session, driver, profile, duration, stop_event, on_transition, clock) -> None:
    """Live capture: stream `getevent` -> Gestures into the session until the deadline / stop_event.
    Lazy device imports so the module stays importable without a phone."""
    import queue
    import subprocess
    import threading

    from adbutils import adb_path

    from wendle.capture.protocols.select import get_protocol
    from wendle.record.stream import stream_gestures

    protocol = get_protocol(profile.touch_protocol)
    x_range = (profile.abs_x[1] - profile.abs_x[0]) or 1
    y_range = (profile.abs_y[1] - profile.abs_y[0]) or 1
    x_scale, y_scale = profile.display[0] / x_range, profile.display[1] / y_range
    swipe_px = 48  # > Android touch slop, in DISPLAY px (a barely-drifting finger stays a TAP)

    gq: "queue.Queue" = queue.Queue()
    stop = threading.Event()
    cmd = [adb_path(), "shell", f"timeout {int(duration) + 5} getevent -lt"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def pump():
        for g in stream_gestures(proc.stdout, protocol, swipe_dist=swipe_px,
                                 x_scale=x_scale, y_scale=y_scale):
            if stop.is_set():
                break
            gq.put(g)

    threading.Thread(target=pump, daemon=True).start()
    deadline = clock() + duration
    try:
        while clock() < deadline and not (stop_event is not None and stop_event.is_set()):
            try:
                g = gq.get(timeout=0.5)
            except queue.Empty:
                continue
            if g.kind == "multi":
                continue
            t = session.record_gesture(g)
            if t is not None and on_transition is not None:
                on_transition(t)
    finally:
        stop.set()
        proc.terminate()
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
