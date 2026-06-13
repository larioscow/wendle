from __future__ import annotations

from typing import Dict, List

from wendle.capture.protocols.base import (
    TouchProtocol,
    hex_value,
    is_lift,
    make_gesture,
)
from wendle.capture.types import Gesture, InputEvent


class TypeBProtocol(TouchProtocol):
    """Linux multi-touch protocol type B (ABS_MT_SLOT + ABS_MT_TRACKING_ID).

    Each finger is a slot; a non-negative ABS_MT_TRACKING_ID marks contact down,
    -1 (any all-F width) marks lift. This is what most modern phones (incl.
    Samsung) emit, and it segments per-finger correctly where the BTN_TOUCH
    decoder would merge taps. Concurrent contacts in >1 slot flag those gestures
    `multi`. A new tracking-id in an already-open slot (implicit replacement)
    closes the old contact first; contacts still open at end of stream are
    flushed as `truncated`.
    """

    name = "type_b"

    def decode(
        self, events: List[InputEvent], *, long_press_s: float = 0.5, swipe_dist: int = 30,
        x_scale: float = 1.0, y_scale: float = 1.0,
    ) -> List[Gesture]:
        gestures: List[Gesture] = []
        slot = 0  # ABS_MT_SLOT is omitted when unchanged; slot 0 is the default
        contacts: Dict[int, dict] = {}
        last_ts = 0.0

        def open_contact(s: int, ts: float) -> None:
            contacts[s] = {"t_down": ts, "sx": None, "sy": None, "lx": None, "ly": None, "multi": False}
            if len(contacts) > 1:  # concurrent fingers → mark all currently open
                for c in contacts.values():
                    c["multi"] = True

        def close_contact(s: int, ts: float, *, truncated: bool = False) -> None:
            c = contacts.pop(s, None)
            if c is None:
                return
            position_missing = c["sx"] is None or c["sy"] is None
            sx = c["sx"] if c["sx"] is not None else 0
            sy = c["sy"] if c["sy"] is not None else 0
            ex = c["lx"] if c["lx"] is not None else sx
            ey = c["ly"] if c["ly"] is not None else sy
            gestures.append(
                make_gesture(
                    t_down=c["t_down"],
                    t_up=ts,
                    sx=sx,
                    sy=sy,
                    ex=ex,
                    ey=ey,
                    multi=c["multi"],
                    position_missing=position_missing,
                    long_press_s=long_press_s,
                    swipe_dist=swipe_dist,
                    truncated=truncated,
                    x_scale=x_scale,
                    y_scale=y_scale,
                )
            )

        for ev in events:
            last_ts = ev.ts
            if ev.code == "ABS_MT_SLOT":
                v = hex_value(ev.value)
                if v is not None:
                    slot = v
            elif ev.code == "ABS_MT_TRACKING_ID":
                if is_lift(ev.value):
                    close_contact(slot, ev.ts)
                else:
                    # implicit finger replacement: close the previous contact first
                    if slot in contacts:
                        close_contact(slot, ev.ts)
                    open_contact(slot, ev.ts)
            elif ev.code == "ABS_MT_POSITION_X":
                v = hex_value(ev.value)
                if v is not None and slot in contacts:
                    c = contacts[slot]
                    if c["sx"] is None:
                        c["sx"] = v
                    c["lx"] = v
            elif ev.code == "ABS_MT_POSITION_Y":
                v = hex_value(ev.value)
                if v is not None and slot in contacts:
                    c = contacts[slot]
                    if c["sy"] is None:
                        c["sy"] = v
                    c["ly"] = v

        # flush any contact still open at end of stream (truncated capture)
        for s in list(contacts.keys()):
            close_contact(s, last_ts, truncated=True)

        return gestures
