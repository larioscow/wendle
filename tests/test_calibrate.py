import os
import stat
from pathlib import Path

from wendle.calibration.calibrate import calibrate
from wendle.driver.fake import FakeDriver
from wendle.models import DeviceProfile

FIX = Path(__file__).parent / "fixtures"


def _driver():
    lp = (FIX / "getevent_lp_pixel.txt").read_text()
    return FakeDriver(shell_outputs={"getevent -lp": lp}, display=(1080, 2340))


def test_calibrate_builds_profile_from_driver():
    profile = calibrate(_driver())
    assert profile.touchscreen_node == "/dev/input/event3"
    assert profile.abs_x == (0, 1080)
    assert profile.abs_y == (0, 2340)
    assert profile.display == (1080, 2340)


def test_calibrate_persists_with_0600_perms(tmp_path):
    out = tmp_path / "device_profile.json"
    profile = calibrate(_driver(), save_to=out)
    assert out.exists()
    mode = stat.S_IMODE(os.stat(out).st_mode)
    assert mode == 0o600
    assert DeviceProfile.from_json(out.read_text()) == profile


def test_calibrate_detects_touch_protocol():
    # the pixel fixture's touchscreen has ABS_MT_SLOT + ABS_MT_TRACKING_ID
    profile = calibrate(_driver())
    assert profile.touch_protocol == "type_b"
