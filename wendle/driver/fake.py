from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from wendle.driver.base import DeviceDriver


class FakeDriver(DeviceDriver):
    """In-memory DeviceDriver for device-free tests.

    Beyond the calibration surface (canned shell output, recorded sendevents), it
    can script a SEQUENCE of hierarchy dumps and dumpsys pairs so the record loop
    and Navigator can be driven deterministically: each `dump_hierarchy()` /
    `dumps()` call advances through the scripted sequence (last entry repeats).
    `present_selectors` backs xpath_exists/resolve_and_tap; injected actions are
    recorded for assertions.
    """

    def __init__(
        self,
        shell_outputs: Dict[str, str] = None,
        display: Tuple[int, int] = (1080, 2400),
        hierarchies: Optional[List[str]] = None,
        dumpsys_pairs: Optional[List[Tuple[str, str]]] = None,
        present_selectors: Optional[set] = None,
        screen_is_on: bool = True,
        checked_states: Optional[dict] = None,
    ):
        self._shell_outputs = shell_outputs or {}
        self._display = display
        self.sent: List[Tuple[str, int, int, int]] = []
        self._hierarchies = list(hierarchies or [])
        self._h_idx = 0
        self._dumpsys = list(dumpsys_pairs or [])
        self._d_idx = 0
        self.present_selectors = present_selectors or set()
        # §4 label union: values whose label resolves to MORE THAN ONE distinct ELEMENT on a
        # real device — resolve_and_tap returns tri-state None (refused-ambiguous). present_
        # selectors CANNOT model this (two same-label nodes collapse to one set entry; a value
        # in several ATTR kinds is ONE element on hardware, which pick_unique_deepest taps), so
        # ambiguity is an EXPLICIT opt-in mirroring the u2 >1-distinct-element semantics.
        self.ambiguous_labels: set = set()
        self.element_centers: Dict[Tuple[str, object], Tuple[int, int]] = {}  # (kind,value) -> center px
        # {(kind, value): n} — element appears after n polls of wait_until_present (test scripting)
        self.present_after: Dict[Tuple[str, object], int] = {}
        self._field_text: Dict[Tuple[str, object], str] = {}  # last value set, for verify_text
        self.verify_fail: set = set()  # selector keys whose verify_text is forced to mismatch
        self._screen_on = screen_is_on
        self._checked_states = dict(checked_states or {})  # selector value -> current bool
        # (package, activity) pairs whose app_start should RAISE — models `am start -n` of a
        # non-exported component being refused (SecurityException / Permission Denial). Lets the
        # launch ladder's raise-path (rung raises -> advance) be exercised device-free.
        self.app_start_raises: set = set()
        self.checked_fail: set = set()    # selector values whose set_checked returns False (won't flip)
        self.checked_raises: set = set()  # selector values whose set_checked raises (element vanished)
        self.checked_vanishes: set = set()  # selector values whose widget vanishes AFTER the tap -> None
        # recorded interactions for assertions
        self.taps: List[Tuple] = []
        self.text_sets: List[Tuple] = []
        self.checked_sets: List[Tuple] = []
        self.keyevents: List[int] = []
        self.presses: List[str] = []
        self.app_starts: List[Tuple] = []
        self.monkey_launches: List[str] = []
        self.swipes: List[Tuple] = []

    # ---- calibration surface ----
    def shell(self, cmd: str) -> str:
        if cmd not in self._shell_outputs:
            raise KeyError(f"FakeDriver has no canned output for: {cmd!r}")
        return self._shell_outputs[cmd]

    def display_size(self) -> Tuple[int, int]:
        return self._display

    def sendevent(self, node: str, type_: int, code: int, value: int) -> None:
        self.sent.append((node, type_, code, value))

    # ---- scripted record/navigate surface ----
    def _advance(self, seq, idx_attr):
        if not seq:
            raise IndexError("FakeDriver has no scripted sequence for this call")
        i = getattr(self, idx_attr)
        val = seq[min(i, len(seq) - 1)]
        setattr(self, idx_attr, i + 1)
        return val

    def dump_hierarchy(self) -> str:
        return self._advance(self._hierarchies, "_h_idx")

    def dumps(self) -> Tuple[str, str]:
        return self._advance(self._dumpsys, "_d_idx")

    def keyevent(self, code: int) -> None:
        self.keyevents.append(code)

    def press(self, key: str) -> None:
        self.presses.append(key)

    def app_start(self, package: str, activity=None, stop: bool = False) -> None:
        self.app_starts.append((package, activity, stop))  # record the attempt, then maybe refuse
        if (package, activity) in self.app_start_raises:
            raise RuntimeError("am start refused: component not exported")

    def launch_monkey(self, package: str) -> None:
        self.monkey_launches.append(package)

    def _key(self, selector):
        return (selector.kind, selector.value)

    def _present(self, selector) -> bool:
        if self._key(selector) in self.present_selectors:
            return True
        if selector.kind == "label":
            # §4 union semantics, mirrored device-free: a label matches a present
            # text / content-desc / hint of the same value
            return any((kind, selector.value) in self.present_selectors
                       for kind in ("text", "content_desc"))  # hint excluded: field handle, not a tap
        return False

    def resolve_and_tap(self, selector, action_type: str = "click", timeout: float = 5.0):
        self.taps.append((selector.kind, selector.value, action_type))
        if selector.kind == "label":
            # Mirror u2's element-keyed deepest/unique resolution: on hardware ONE element
            # exposing the same value through several attrs (text==content-desc, the common
            # accessibility shape) is a UNIQUE match — attr-counting would wrongly refuse it
            # (the mock-contract divergence class). Ambiguity (>1 DISTINCT element) is
            # modeled EXPLICITLY via self.ambiguous_labels -> tri-state None.
            if selector.value in self.ambiguous_labels:
                return None
            return self._present(selector)
        return self._present(selector)

    def set_text(self, selector, value: str) -> None:
        self.text_sets.append((selector.kind, selector.value, value))

    def type_text(self, selector, value: str) -> None:
        self.text_sets.append((selector.kind, selector.value, value, "per_key"))

    def swipe(self, start, end, duration: float = 0.2) -> None:
        self.swipes.append((tuple(start), tuple(end)))

    def element_center(self, selector):
        if selector.kind == "coords":
            return tuple(selector.value)
        # canned center for any present element; tests can override via element_centers
        if self._present(selector):
            return self.element_centers.get(self._key(selector), (540, 1200))
        return None

    def focus_and_type(self, selector, value: str, *, clear: bool = True) -> bool:
        # the engine gates presence via wait_until_present first, so here we just record the
        # focus-then-type and remember the field's text so verify_text can confirm it landed.
        self.text_sets.append((selector.kind, selector.value, value, "focus_and_type"))
        self._field_text[self._key(selector)] = value
        return True

    def verify_text(self, selector, expected: str, *, masked: bool = False, timeout: float = 3.0,
                    interval: float = 0.2, clock=None, sleep=None) -> bool:
        key = self._key(selector)
        if key in self.verify_fail:
            return False
        cur = self._field_text.get(key, "")
        return len(cur) > 0 if masked else cur == expected

    def set_checked(self, selector, target: bool, *, settle_timeout: float = 8.0,
                    interval: float = 0.2, clock=None, sleep=None):
        if selector.value in self.checked_raises:
            raise RuntimeError("checkbox element vanished")
        if selector.value in self.checked_vanishes:
            # tapped, then the widget went away (self-dismissing gate) -> tri-state None
            self.checked_sets.append((selector.kind, selector.value, target, "vanished"))
            return None
        if selector.value in self.checked_fail:
            self.checked_sets.append((selector.kind, selector.value, target, "drift"))
            return False  # tapped but never reached the target state (honest drift)
        cur = self._checked_states.get(selector.value, False)
        if cur != target:
            self._checked_states[selector.value] = target
            self.checked_sets.append((selector.kind, selector.value, target))  # a real flip
        else:
            self.checked_sets.append((selector.kind, selector.value, target, "noop"))  # idempotent
        return True

    def tap_contains(self, kind: str, substring: str, action_type: str = "click", timeout: float = 5.0) -> bool:
        self.taps.append((kind, "contains:" + substring, action_type))
        # a 'label' contains-match searches the union (text/content_desc/hint), mirroring u2.
        kinds = ("text", "content_desc") if kind == "label" else (kind,)
        return any(k in kinds and substring in str(v) for (k, v) in self.present_selectors)

    def xpath_exists(self, selector) -> bool:
        return self._present(selector)

    def wait_until_present(self, selector, timeout: float = 10.0, *, interval: float = 0.2,
                           clock=None, sleep=None) -> bool:
        if selector.kind in ("coords", "keyevent"):
            return True
        clock = clock or time.monotonic
        sleep = sleep or time.sleep
        key = self._key(selector)
        deadline = clock() + timeout
        while True:
            if self._present(selector):
                return True
            n = self.present_after.get(key)
            if n is not None:
                if n <= 0:
                    self.present_selectors.add(key)
                    return True
                self.present_after[key] = n - 1
            if clock() >= deadline:
                return False
            sleep(interval)

    def wait_activity(self, activity: str, timeout: float = 10.0) -> bool:
        return True

    def wait_gone(self, selector, timeout: float = 10.0) -> bool:
        return True

    def screen_on(self) -> bool:
        return self._screen_on

    def screenshot(self, path=None) -> bytes:
        return b""
