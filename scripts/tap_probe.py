"""Diagnostic: capture raw getevent taps and show raw->fraction->pixel scaling.

    uv run python scripts/tap_probe.py [seconds]

Tap known spots; compare the printed fraction to where you physically tapped.
"""
from __future__ import annotations

import subprocess
import sys


def main(duration: float = 20.0) -> int:
    import uiautomator2 as u2
    from adbutils import adb_path

    from wendle.calibration.calibrate import calibrate
    from wendle.calibration.scaling import scale_to_pixels
    from wendle.capture.protocols.select import get_protocol
    from wendle.driver.u2_driver import U2Driver
    from wendle.record.stream import stream_gestures

    u2.connect()
    d = U2Driver()
    p = calibrate(d)
    proto = get_protocol(p.touch_protocol)
    print(f"profile abs_x={p.abs_x} abs_y={p.abs_y} display={p.display} protocol={p.touch_protocol}")
    print("TAP: (1) center  (2) top-left corner  (3) bottom-right corner  (4) bottom-center\n")

    cmd = [adb_path(), "shell", f"timeout {int(duration) + 4} getevent -lt"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    for g in stream_gestures(proc.stdout, proto):
        if g.kind == "multi":
            continue
        px = scale_to_pixels(g.x, abs_min=p.abs_x[0], abs_max=p.abs_x[1], screen=p.display[0])
        py = scale_to_pixels(g.y, abs_min=p.abs_y[0], abs_max=p.abs_y[1], screen=p.display[1])
        fx = g.x / p.abs_x[1] if p.abs_x[1] else 0
        fy = g.y / p.abs_y[1] if p.abs_y[1] else 0
        print(f"{g.kind:9s} raw=({g.x:5d},{g.y:5d})  frac=({fx:.2f},{fy:.2f})  px=({px:4d},{py:4d})")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(float(args[0]) if args else 20.0))
