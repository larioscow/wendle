from __future__ import annotations

from wendle.capture.protocols.base import TouchProtocol
from wendle.capture.protocols.btn_touch import BtnTouchProtocol
from wendle.capture.protocols.type_b import TypeBProtocol
from wendle.models import InputDevice

PROTOCOLS = {
    "type_b": TypeBProtocol,
    "btn_touch": BtnTouchProtocol,
}


def detect_protocol_name(touchscreen: InputDevice) -> str:
    """Pick the evdev touch protocol from the device's reported capabilities.

    Type B is identified by ABS_MT_SLOT + ABS_MT_TRACKING_ID; otherwise we fall
    back to BTN_TOUCH-driven decoding. Keyed on protocol, never on brand.
    """
    axes = touchscreen.abs_axes
    if "ABS_MT_SLOT" in axes and "ABS_MT_TRACKING_ID" in axes:
        return "type_b"
    return "btn_touch"


def get_protocol(name: str) -> TouchProtocol:
    """Instantiate a protocol decoder by name (as stored in DeviceProfile)."""
    try:
        return PROTOCOLS[name]()
    except KeyError:
        raise ValueError(f"unknown touch protocol: {name!r}")


def select_protocol(touchscreen: InputDevice) -> TouchProtocol:
    return get_protocol(detect_protocol_name(touchscreen))
