import json

from wendle.models import AbsAxis, DeviceProfile, InputDevice


def test_device_profile_json_round_trip():
    profile = DeviceProfile(
        touchscreen_node="/dev/input/event3",
        abs_x=(0, 1080),
        abs_y=(0, 2340),
        display=(1080, 2340),
        timebase_validated=True,
    )
    blob = profile.to_json()
    restored = DeviceProfile.from_json(blob)
    assert restored == profile
    assert json.loads(blob)["touchscreen_node"] == "/dev/input/event3"


def test_input_device_holds_abs_axes():
    dev = InputDevice(
        path="/dev/input/event3",
        name="ft5x46",
        abs_axes={"ABS_MT_POSITION_X": AbsAxis(min=0, max=1080)},
    )
    assert dev.abs_axes["ABS_MT_POSITION_X"].max == 1080
