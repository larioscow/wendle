"""Spike 1 KILL GATE — measured selector-recovery + dump-latency on a real device.

    uv run python scripts/spike1_gate.py [seconds]

Tap deliberately on real buttons across a few screens for the duration (default
30s, aim for >= 20 taps). Prints a PASS/FAIL report (§14). Run on >= 2 devices,
including a battery-optimized OEM phone.

Design: during capture, a poller thread only COLLECTS raw (host-time) dumps and a
getevent stream is read on the main thread. After the session the ring buffer is
built single-threaded (no locks), taps are bound in the device CLOCK_MONOTONIC
clock (offset-aligned from the first event), and warm-up taps before the first
dump are excluded. This measures the cheap getevent+hierarchy floor (no a11y
enrichment), so the gate is conservative — real reliability is >= what it reports.
"""

from __future__ import annotations

import select
import subprocess
import sys
import threading
import time
from typing import List, Optional, Tuple


def main(duration: float = 30.0, debug: bool = False) -> int:
    try:
        import uiautomator2 as u2
        from adbutils import adb_path
    except Exception as e:  # noqa: BLE001
        print("Run via `uv run`. Import error:", e)
        return 1

    from wendle.calibration.calibrate import calibrate
    from wendle.capture.events import parse_getevent_stream
    from wendle.capture.gestures import segment_gestures
    from wendle.capture.hierarchy import parse_hierarchy
    from wendle.capture.protocols.select import get_protocol
    from wendle.capture.recorder import detect_action
    from wendle.capture.types import Snapshot
    from wendle.driver.u2_driver import U2Driver
    from wendle.gate.binding import bind_latest
    from wendle.gate.metrics import TapResult, compute_report

    print("Connecting + calibrating...")
    d = u2.connect()
    profile = calibrate(U2Driver())
    protocol = get_protocol(profile.touch_protocol)
    print(f"  protocol={protocol.name}  node={profile.touchscreen_node}")

    serial = getattr(d, "serial", None)
    # device-side `timeout` so getevent self-terminates even if teardown is rough
    getevent = f"timeout {int(duration) + 5} getevent -lt"
    cmd = [adb_path()] + (["-s", serial] if serial else []) + ["shell", getevent]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    raw_snaps: List[Tuple[float, float, list]] = []  # (t0_host, t1_host, nodes)
    latencies: List[float] = []
    empty_dumps = 0
    total_dumps = 0
    stop = threading.Event()

    def poll_dumps() -> None:
        nonlocal empty_dumps, total_dumps
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                xml = d.dump_hierarchy()
            except Exception:  # noqa: BLE001
                xml = ""
            t1 = time.monotonic()
            total_dumps += 1
            latencies.append(t1 - t0)
            nodes = parse_hierarchy(xml) if xml.strip() else []
            if not nodes:
                empty_dumps += 1
                continue
            raw_snaps.append((t0, t1, nodes))

    poller = threading.Thread(target=poll_dumps, daemon=True)
    poller.start()

    print(f"\nTAP DELIBERATELY ON BUTTONS FOR {int(duration)}s — across a few screens...\n")
    raw_lines: List[str] = []
    offset: Optional[float] = None  # host_monotonic - device_ts
    deadline = time.time() + duration
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.3)
        if not ready:
            continue
        line = proc.stdout.readline()
        if not line:
            break
        raw_lines.append(line)
        if offset is None:
            evs = parse_getevent_stream(line)
            if evs:
                offset = time.monotonic() - evs[0].ts

    stop.set()
    proc.terminate()
    try:
        tail = proc.communicate(timeout=3)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        tail = proc.communicate()[0]
    if tail:
        raw_lines.append(tail)  # don't drop the last buffered taps
    poller.join()  # unconditional; loop exits within one dump of stop.set()

    if offset is None:
        print("No input events captured — see earlier notes; nothing to score.")
        return 1

    # Snapshots in the device clock, sorted by completion time. The harness polls
    # dumps continuously, so a tap binds to the freshest dump completed before it
    # (gate.binding.bind_latest) rather than the recorder's dump-on-transition
    # windows.
    snaps = sorted(
        (
            Snapshot(t_start=t0 - offset, t_end=t1 - offset, hierarchy_hash="", nodes=nodes)
            for t0, t1, nodes in raw_snaps
        ),
        key=lambda s: s.t_end,
    )
    first_dev_t: Optional[float] = snaps[0].t_end if snaps else None

    from wendle.calibration.scaling import scale_to_pixels
    from wendle.capture.hierarchy import node_at

    events = parse_getevent_stream("".join(raw_lines))
    gestures = segment_gestures(events, protocol=protocol)
    taps: List[TapResult] = []
    n_multi = n_warmup = n_swipe = 0
    if debug:
        print("\n--- per-tap detail ---")
    for g in gestures:
        if g.kind == "multi":
            n_multi += 1
            continue
        if g.kind == "swipe":
            n_swipe += 1  # a scroll, not an element tap — not scored for recovery
            continue
        if first_dev_t is not None and g.t_up < first_dev_t:
            n_warmup += 1  # before any dump completed — not a fair measurement
            continue
        snap, confidence = bind_latest(snaps, g.t_up)
        if snap is None:
            taps.append(TapResult(bound=False, replayability="none", needs_confirmation=True))
            if debug:
                print(f"  {g.kind:9s} UNBOUND (no dump before tap)")
            continue
        action, needs = detect_action(g, snap, profile, bind_confidence=confidence)
        taps.append(TapResult(bound=True, replayability=action.replayability, needs_confirmation=needs))
        if debug:
            px = scale_to_pixels(g.x, abs_min=profile.abs_x[0], abs_max=profile.abs_x[1], screen=profile.display[0])
            py = scale_to_pixels(g.y, abs_min=profile.abs_y[0], abs_max=profile.abs_y[1], screen=profile.display[1])
            node = node_at(snap.nodes, px, py)
            if node is None:
                desc = "node_at=None (tap outside all nodes — dead space)"
            else:
                desc = (f"cls={node.cls.split('.')[-1]} id={node.resource_id or '-'} "
                        f"text={node.text[:20]!r} desc={node.content_desc[:20]!r} clickable={node.clickable}")
            sel = action.selector
            selstr = "<redacted>" if action.sensitive else f"{sel.kind}={sel.value!r}"
            print(f"  {g.kind:9s} {action.replayability:15s} sel={selstr} @({px},{py}) {desc}")

    report = compute_report(taps, latencies, empty_dumps, total_dumps)
    print("\n==== Spike 1 gate report ====")
    print(report.render())
    if n_warmup or n_multi or n_swipe:
        print(f"(excluded from tap score: {n_swipe} swipes/scrolls, "
              f"{n_warmup} warm-up, {n_multi} multi-finger)")
    print("=============================")
    if not report.passed and len(taps) < 5:
        print("\n(Too few taps scored — tap more next run; this isn't a real FAIL.)")
    return 0 if report.passed else 2


if __name__ == "__main__":
    args = sys.argv[1:]
    debug = "--debug" in args
    nums = [a for a in args if a.replace(".", "", 1).isdigit()]
    dur = float(nums[0]) if nums else 30.0
    sys.exit(main(dur, debug=debug))
