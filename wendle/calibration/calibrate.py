from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Optional

from wendle.calibration.getevent_parse import parse_getevent_lp
from wendle.calibration.node_discovery import find_touchscreen
from wendle.capture.protocols.select import detect_protocol_name
from wendle.driver.base import DeviceDriver
from wendle.models import DeviceProfile


def calibrate(driver: DeviceDriver, save_to: Optional[Path] = None) -> DeviceProfile:
    """Build (and optionally persist) a DeviceProfile from a connected driver.

    Pure logic over the driver port, so it is fully testable with FakeDriver.
    Persists with 0600 perms (the profile is device-identifying, §3.4).
    """
    devices = parse_getevent_lp(driver.shell("getevent -lp"))
    ts = find_touchscreen(devices)
    x = ts.abs_axes["ABS_MT_POSITION_X"]
    y = ts.abs_axes["ABS_MT_POSITION_Y"]

    # A degenerate axis (min == max) would make scaling impossible; fail now at
    # calibration rather than crashing at the first tap.
    if x.min >= x.max or y.min >= y.max:
        raise ValueError(
            f"touchscreen {ts.path} has a degenerate axis: "
            f"X[{x.min},{x.max}] Y[{y.min},{y.max}]"
        )

    width, height = driver.display_size()

    # §5: panel-max need not equal display resolution. An off-by-one between
    # panel max and pixel count is normal and benign; only warn on a real
    # mismatch (panel max is neither `width` nor `width-1`).
    if abs(x.max + 1 - width) > 1 or abs(y.max + 1 - height) > 1:
        warnings.warn(
            f"touch-panel max ({x.max}x{y.max}) differs materially from display "
            f"({width}x{height}); scaling uses the panel axes — verify on-device"
        )

    profile = DeviceProfile(
        touchscreen_node=ts.path,
        abs_x=(x.min, x.max),
        abs_y=(y.min, y.max),
        display=(width, height),
        timebase_validated=False,  # set True only after the on-device timebase gate
        touch_protocol=detect_protocol_name(ts),
    )
    if save_to is not None:
        save_to = Path(save_to)
        save_to.write_text(profile.to_json())
        os.chmod(save_to, 0o600)
    return profile
