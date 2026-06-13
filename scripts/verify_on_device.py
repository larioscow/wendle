"""Manual on-device smoke check for Spikes 0 + 1.

Run with a phone plugged in (USB debugging on):

    uv run python scripts/verify_on_device.py

It will: connect, calibrate, parse the current screen, then capture YOUR taps for
8 seconds and show what it detected. Nothing is recorded to disk.
"""

from __future__ import annotations

import subprocess
import sys
import time


def step(n: str) -> None:
    print(f"\n=== {n} ===")


def main() -> int:
    try:
        import uiautomator2 as u2
    except Exception as e:  # noqa: BLE001
        print("uiautomator2 not importable — run via `uv run`. Error:", e)
        return 1

    step("1) Connect to the device")
    try:
        d = u2.connect()  # first run pushes the u2 agent to the phone
        info = d.info
        print(f"   OK — {info.get('productName')} "
              f"{info.get('displayWidth')}x{info.get('displayHeight')}, "
              f"Android {d.device_info.get('version') if hasattr(d, 'device_info') else '?'}")
    except Exception as e:  # noqa: BLE001
        print("   FAILED to connect. Is the phone plugged in, USB-debugging on, and authorized?")
        print("   Error:", e)
        return 1

    from wendle.calibration.calibrate import calibrate
    from wendle.calibration.scaling import scale_to_pixels
    from wendle.capture.events import parse_getevent_stream
    from wendle.capture.gestures import segment_gestures
    from wendle.capture.hierarchy import node_at, parse_hierarchy
    from wendle.driver.u2_driver import U2Driver

    step("2) Calibrate (Spike 0) — find the touchscreen + coordinate scale")
    try:
        profile = calibrate(U2Driver())
        print("   OK — device profile:")
        for line in profile.to_json().splitlines():
            print("   " + line)
    except Exception as e:  # noqa: BLE001
        print("   FAILED:", e)
        return 1

    step("3) Parse the CURRENT screen (Spike 1 hierarchy)")
    try:
        xml = d.dump_hierarchy()
        nodes = parse_hierarchy(xml)
        w, h = profile.display
        hit = node_at(nodes, w // 2, h // 2)
        print(f"   OK — parsed {len(nodes)} UI nodes")
        if hit is not None:
            print(f"   element at screen centre: class={hit.cls!r} "
                  f"id={hit.resource_id!r} text={hit.text!r} desc={hit.content_desc!r}")
        else:
            print("   (nothing at screen centre)")
    except Exception as e:  # noqa: BLE001
        print("   FAILED:", e)
        return 1

    step("4) Capture YOUR taps for 8 seconds — TAP AROUND THE SCREEN NOW")
    # Capture from ALL input nodes (no node arg) so we don't depend on picking the
    # right one; the parser ignores the node prefix and gestures filter by event.
    import select

    serial = getattr(d, "serial", None)
    try:
        from adbutils import adb_path

        adb = adb_path()
    except Exception:  # noqa: BLE001
        adb = "adb"
    cmd = [adb] + (["-s", serial] if serial else []) + ["shell", "getevent", "-lt"]
    print("   running:", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
        )
    except Exception as e:  # noqa: BLE001
        print("   FAILED to launch adb:", e)
        return 1

    lines = []
    deadline = time.time() + 8
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.3)
        if ready:
            line = proc.stdout.readline()
            if line == "":  # process ended
                break
            lines.append(line)
    proc.terminate()
    try:
        err = proc.communicate(timeout=3)[1]
    except subprocess.TimeoutExpired:
        proc.kill()
        err = proc.communicate()[1]

    out = "".join(lines)
    if not lines and err:
        print("   getevent stderr (first lines):")
        for ln in err.splitlines()[:6]:
            print("     " + ln)
    from wendle.capture.protocols.select import get_protocol

    protocol = get_protocol(profile.touch_protocol)
    print(f"   decoding with touch protocol: {protocol.name}")
    events = parse_getevent_stream(out or "")
    gestures = segment_gestures(events, protocol=protocol)
    print(f"   captured {len(events)} input events -> {len(gestures)} gestures")
    for g in gestures[:15]:
        px = scale_to_pixels(g.x, abs_min=profile.abs_x[0], abs_max=profile.abs_x[1],
                             screen=profile.display[0])
        py = scale_to_pixels(g.y, abs_min=profile.abs_y[0], abs_max=profile.abs_y[1],
                             screen=profile.display[1])
        flag = " (position_missing!)" if g.position_missing else ""
        print(f"   {g.kind:10s} raw=({g.x},{g.y}) -> pixel=({px},{py}){flag}")

    if not gestures:
        print("\n   No gestures detected.")
        if events:
            print(f"   BUT {len(events)} raw events WERE captured — the segmenter just doesn't")
            print("   recognize this device's touch vocabulary. First captured lines:")
            for ln in out.splitlines()[:25]:
                print("     " + ln)
            print("   ^ share these lines so the segmenter can be adapted.")
        else:
            print("   Zero raw events captured either — see the getevent stderr above, and")
            print("   share whatever printed.")
    else:
        print("\n   Sanity check: do the pixel coordinates above match where you actually tapped?")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
