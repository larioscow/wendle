from __future__ import annotations

import ast
import inspect
import textwrap
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Optional, Tuple


@lru_cache(maxsize=None)
def _is_not_implemented_stub(fn) -> bool:
    """True when `fn`'s body is nothing but `raise NotImplementedError` — an unimplemented
    optional-capability stub, whether INHERITED from the base or RE-DECLARED on a subclass.
    AST-based (not 'is this a different object than the base stub'), so a re-declared raising
    stub is still seen as unsupported, while a real method that merely raises in some branch is
    NOT a stub. Unparseable functions (lambdas, C builtins) are treated as real."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return False
    node = tree.body[0] if tree.body else None
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = [n for n in node.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]  # drop docstring
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    exc = body[0].exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"


class DeviceDriver(ABC):
    """Seam isolating all device I/O so the core (calibration, fingerprint, record,
    navigate) stays pure and unit-testable against FakeDriver.

    `shell`, `display_size`, `sendevent` are the calibration core (abstract). The
    record/navigate methods below have NotImplementedError defaults so a driver
    only implements what it needs; U2Driver implements all, FakeDriver scripts the
    subset the tests exercise.

    DUMP DISCIPLINE: every method that dumps the hierarchy or dumpsys is a single
    serialized channel on-device; callers MUST hold the session/navigator's shared
    dump lock around `dump_hierarchy` / `dumps` (§6 dump-contention).
    """

    def supports(self, capability: str) -> bool:
        """True when this driver actually IMPLEMENTS `capability` — i.e. the resolved method is a
        real body, NOT a `raise NotImplementedError` stub (whether inherited from the base or
        re-declared on a subclass). The single declared gate for optional methods (`type_text`,
        `launch_monkey`, …), replacing scattered `hasattr` probes that always read True because the
        stub exists on the base. Class-level by design: a capability is a property of the driver
        type, not a per-instance monkeypatch."""
        own = getattr(type(self), capability, None)
        return callable(own) and not _is_not_implemented_stub(own)

    @abstractmethod
    def shell(self, cmd: str) -> str:
        """Run an adb shell command, return stdout as text."""

    @abstractmethod
    def display_size(self) -> Tuple[int, int]:
        """Return (width, height) in pixels."""

    @abstractmethod
    def sendevent(self, node: str, type_: int, code: int, value: int) -> None:
        """Inject one raw input event on the given /dev/input node."""

    # ---- record / navigate surface (override as needed) ----

    def dump_hierarchy(self) -> str:
        """Return the current UIAutomator hierarchy XML (under the shared lock)."""
        raise NotImplementedError

    def dumps(self) -> Tuple[str, str]:
        """One fetch of (dumpsys activity activities, dumpsys window) — derive both
        namespace and focused package from this single pair (no extra shells)."""
        raise NotImplementedError

    def keyevent(self, code: int) -> None:
        raise NotImplementedError

    def press(self, key: str) -> None:
        """A named key: 'home' / 'back' / 'recent'."""
        raise NotImplementedError

    def app_start(self, package: str, activity: Optional[str] = None, stop: bool = False) -> None:
        raise NotImplementedError

    def launch_monkey(self, package: str) -> None:
        """Fire the package's MAIN/LAUNCHER entry via monkey (`monkey -p pkg -c
        android.intent.category.LAUNCHER 1`). Distinct from app_start(pkg, None): monkey resolves
        through PackageManager on the MAIN/LAUNCHER intent rather than a NAMED `-n` component, so
        it bypasses the per-component exported check that refuses `am start -n` of a non-exported
        single entry. (For a MULTI-launcher-entry package it resolves the package DEFAULT — the
        wrong surface for a secondary entry like Gemini — so the launch gate must still verify.)"""
        raise NotImplementedError

    def resolve_and_tap(self, selector, action_type: str = "click", timeout: float = 5.0):
        """Resolve a Selector via the ladder and tap/long-click/swipe. TRI-STATE: True it hit,
        False not present, None refused-AMBIGUOUS (a label union matched >1 distinct element —
        never a first-match tap; the caller stops typed). Non-label selectors return bool."""
        raise NotImplementedError

    def tap_contains(
        self, kind: str, substring: str, action_type: str = "click", timeout: float = 5.0
    ) -> bool:
        """Tap an element whose text/content-desc CONTAINS `substring` — the fallback for
        a recorded label that decayed (e.g. a chat row 'Name, sent 53 min ago' whose
        timestamp changed). True if it hit a match."""
        raise NotImplementedError

    def set_text(self, selector, value: str) -> None:
        raise NotImplementedError

    def type_text(self, selector, value: str) -> None:
        """Per-keystroke text entry (drives the input pipeline so a TextWatcher /
        search-as-you-type fires per char), for fields where atomic set_text no-ops."""
        raise NotImplementedError

    def set_checked(self, selector, target: bool, *, settle_timeout: float = 8.0,
                    interval: float = 0.2, clock=None, sleep=None) -> "Optional[bool]":
        """Idempotently set a checkbox/switch/radio to `target`: read live checked/selected
        and tap ONLY if it differs (Playwright setChecked / Appium if-not-selected-click),
        so replay never double-flips. Polls until the state lands; clock/sleep injectable.
        TRI-STATE: True on success, False on honest drift (still present, never reached
        target), None when the widget VANISHED after the tap — the flip is unverifiable
        (a self-dismissing gate), distinct from both success and drift."""
        raise NotImplementedError

    def xpath_exists(self, selector) -> bool:
        """Cheap existence probe for a Selector (no tap)."""
        raise NotImplementedError

    # ---- replay-engine waits (Maestro-style: POLL until the condition, never blind-sleep;
    # clock/sleep are injectable so device-free tests run at ~0 wall-time) ----

    def wait_until_present(self, selector, timeout: float = 10.0, *, interval: float = 0.2,
                           clock=None, sleep=None) -> bool:
        """Poll the live UI until `selector`'s element appears; return True the INSTANT it
        does, False at `timeout`. Coordinate/keyevent selectors (no element) return True."""
        raise NotImplementedError

    def swipe(self, start, end, duration: float = 0.2) -> None:
        """Drag from (sx,sy) to (ex,ey) over `duration` seconds — replays a recorded swipe."""
        raise NotImplementedError

    def element_center(self, selector) -> Optional[Tuple[int, int]]:
        """The (x,y) pixel center of `selector`'s element, or None if absent. Used to reconstruct
        a swipe START from a semantically-anchored swipe (older recordings stored the start
        element's label instead of its coordinates)."""
        raise NotImplementedError

    def focus_and_type(self, selector, value: str, *, clear: bool = True) -> bool:
        """Tap the field to FOCUS it (raise the IME), optionally clear, then type per-keystroke
        through the IME (never a shell string — injection + leak). False if the field is absent."""
        raise NotImplementedError

    def verify_text(self, selector, expected: str, *, masked: bool = False, timeout: float = 3.0,
                    interval: float = 0.2, clock=None, sleep=None) -> bool:
        """Re-read the field's live text and confirm the value landed (poll until match or
        timeout). `masked` fields assert presence-of-any-text, NEVER comparing the secret."""
        raise NotImplementedError

    def wait_activity(self, activity: str, timeout: float = 10.0) -> bool:
        raise NotImplementedError

    def wait_gone(self, selector, timeout: float = 10.0) -> bool:
        raise NotImplementedError

    def screen_on(self) -> bool:
        raise NotImplementedError

    def screenshot(self, path: Optional[str] = None) -> bytes:
        raise NotImplementedError
