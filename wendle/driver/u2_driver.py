from __future__ import annotations

import re
import time
from typing import Optional, Tuple

from wendle.driver.base import DeviceDriver

# An Android input-method id is `package/.Class` — a fixed device-derived charset with NO shell
# metacharacters. Validated before it is ever interpolated into an `ime set` command so the IME
# restore can never become a shell-injection sink (invariant #4), even though the value is device
# state, not recorded/credential input.
_IME_ID_RE = re.compile(r"^[\w./-]+$")

# A package or activity name: java identifier chars + dots (+ `$` inner classes). Validated
# before interpolation into `am start` so a recorded namespace can never become a shell-injection
# sink (invariant #4).
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._$]+$")
# `am start` refusal markers (a non-exported component, a missing class): the command "succeeds"
# at the exit-code level while printing the error — exit codes lie, the TEXT is the signal.
_AM_REFUSED = ("Permission Denial", "Error", "Exception")


def selector_to_xpath(selector) -> str:
    """Map a Selector to a u2 v3 xpath string (§8 ladder, library shim).

    `resource-id` values pass through VERBATIM — both `pkg:id/name` and raw test tags
    (Compose `testTagsAsResourceId`) are valid ids; synthesizing or validating a package
    prefix is the Appium `disableIdLocatorAutocompletion` bug class (§4)."""
    kind, value = selector.kind, selector.value
    if kind == "text":
        return f'//*[@text={_q(value)}]'
    if kind == "content_desc":
        return f'//*[@content-desc={_q(value)}]'
    if kind == "resource_id":
        return f'//*[@resource-id={_q(value)}]'
    if kind == "hint":
        # the field's placeholder — the stable handle of a pure-Compose text field (§4);
        # `hint` is a real dump attribute (S23-probe-confirmed, API 26+)
        return f'//*[@hint={_q(value)}]'
    if kind == "label":
        # §4 label-union (Maestro's `text:` semantics): a recorded text anchor matches
        # text ∪ content-desc — the label may have migrated between those attributes by
        # replay time. HINT IS EXCLUDED (S23 confident-wrong sighting): an input's hint is
        # a FIELD handle, and Samsung Settings ROTATES search-plate hints through setting
        # names — a tap-class label resolving via a hint taps the search box instead of the
        # (off-screen) row. Fields keep their own `hint` kind. Resolution is deepest-match +
        # UNIQUE-or-refuse; recorded `text`/`content_desc` selectors stay exact-attribute.
        return (f'//*[@text={_q(value)} or @content-desc={_q(value)}]')
    if kind == "xpath":
        return value
    raise ValueError(f"coords/unknown selector has no xpath form: {kind}")


def pick_unique_deepest(matches) -> Optional[object]:
    """Deepest-match + uniqueness for label-union resolution (§4), as a PURE function.

    `matches`: objects exposing `.elem` (an lxml element with `getparent()`). Drops every
    match that is an ANCESTOR of another match (Maestro's deepest-node rule — a row whose
    text merged upward must not shadow its leaf), then requires EXACTLY ONE survivor:
    items in feeds legally repeat, and tapping the first of two lookalikes is the
    cardinal-sin path — ambiguity refuses (None), it never guesses."""
    if not matches:
        return None
    elems = [getattr(m, "elem", None) for m in matches]
    survivors = []
    for i, m in enumerate(matches):
        is_ancestor = False
        for j, other in enumerate(elems):
            if i == j or other is None:
                continue
            cur = other.getparent() if hasattr(other, "getparent") else None
            while cur is not None:
                if cur is elems[i]:
                    is_ancestor = True
                    break
                cur = cur.getparent()
            if is_ancestor:
                break
        if not is_ancestor:
            survivors.append(m)
    return survivors[0] if len(survivors) == 1 else None


def _q(s: str) -> str:
    return '"' + str(s).replace('"', '\\"') + '"'


class U2Driver(DeviceDriver):
    """Real DeviceDriver backed by uiautomator2 + adb. Requires a connected device."""

    def __init__(self, serial: str | None = None, *, device=None):
        # `device` is a test seam: inject a stub that mirrors the uiautomator2 surface so the
        # device-facing methods get device-free contract coverage (a u2 API-shape mismatch must
        # fail in CI, not silently ship and crash on hardware — see tests/test_u2_driver_contract).
        if device is not None:
            self._d = device
            return
        import uiautomator2 as u2  # imported lazily so tests need no device/u2

        self._d = u2.connect(serial)

    def shell(self, cmd: str) -> str:
        output, _ = self._d.shell(cmd)  # u2 v3 returns (output, exit_code)
        return output

    def display_size(self) -> Tuple[int, int]:
        info = self._d.info
        return int(info["displayWidth"]), int(info["displayHeight"])

    def sendevent(self, node: str, type_: int, code: int, value: int) -> None:
        self._d.shell(f"sendevent {node} {type_} {code} {value}")

    def dump_hierarchy(self) -> str:
        return self._d.dump_hierarchy()  # u2 retries 3x on HierarchyEmptyError

    def dumps(self) -> Tuple[str, str]:
        return self.shell("dumpsys activity activities"), self.shell("dumpsys window")

    def keyevent(self, code: int) -> None:
        self.shell(f"input keyevent {code}")

    def press(self, key: str) -> None:
        self._d.press(key)

    def app_start(self, package: str, activity=None, stop: bool = False) -> None:
        """Start an app, with the launch ladder's physics honored on REAL hardware:

        - explicit activity -> our own `am start -n` with refusal detection, RAISING on a
          refused/non-exported component. u2's app_start DISCARDS the am output, so the
          raise-=-skip contract FakeDriver models (app_start_raises) was a signal the real
          driver never delivered — a doomed recorded component silently burned the gate's
          whole window instead of advancing the ladder instantly.
        - activity=None -> resolve the package's LAUNCHER entry and am-start it (the base
          contract: package-default is DISTINCT from the monkey rung), falling back to u2's
          monkey-based launch only when resolution fails.
        """
        if not _COMPONENT_RE.match(package) or (activity is not None and not _COMPONENT_RE.match(activity)):
            raise ValueError("invalid Android component name")  # never a shell-injection sink
        if stop:
            self._d.app_stop(package)
        if activity is not None:
            out = self.shell(f"am start -n {package}/{activity}") or ""
            if any(marker in out for marker in _AM_REFUSED):
                raise RuntimeError(f"am start refused: {package}/{activity}")
            return
        lines = (self.shell(f"cmd package resolve-activity --brief {package}") or "").strip().splitlines()
        comp = lines[-1].strip() if lines else ""
        pkg, _, act = comp.partition("/")
        if act and _COMPONENT_RE.match(pkg) and _COMPONENT_RE.match(act):
            out = self.shell(f"am start -n {comp}") or ""
            if not any(marker in out for marker in _AM_REFUSED):
                return
        self._d.app_start(package)  # unresolvable/refused entry: u2's monkey LAUNCHER launch

    def launch_monkey(self, package: str) -> None:
        # Android package names are [a-zA-Z0-9_.]+ (no shell metacharacters possible), so this
        # is not a command-injection sink; the category constant is a fixed literal.
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    def resolve_and_tap(self, selector, action_type: str = "click", timeout: float = 5.0) -> bool:
        if selector.kind == "coords":
            x, y = selector.value
            # honor the action_type: a coordinate long-press (the reveal rung bounds-anchors a
            # revealed long_click here) must NOT silently degrade to a short click.
            if action_type == "long_click":
                self._d.long_click(x, y)
            else:
                self._d.click(x, y)  # coordinate_only — caller warns before reaching here
            return True
        if selector.kind == "label":
            # §4 union resolution: deepest match, UNIQUE-or-refuse. TRI-STATE (set_checked
            # precedent): False = not present, None = AMBIGUOUS (>1 distinct element — refuse
            # rather than tap a lookalike; the engine stops typed naming the ambiguity), an
            # element = unique -> tap. pick_unique_deepest returns None only for the >1 case
            # once matches exist (the deepest leaf always survives ancestor-filtering).
            xp = self._d.xpath(selector_to_xpath(selector))
            if not xp.wait(timeout=timeout):
                return False
            matches = xp.all()
            if not matches:
                return False
            target = pick_unique_deepest(matches)
            if target is None:
                return None  # ambiguous union — refused, never first-match
            cx, cy = target.center()
            if action_type == "long_click":
                self._d.long_click(cx, cy)
            else:
                self._d.click(cx, cy)
            return True
        xp = self._d.xpath(selector_to_xpath(selector))
        if action_type == "long_click":
            # u2 v3: .wait() is a BOOL gate; long_click lives on the SELECTOR, not the element.
            if not xp.wait(timeout=timeout):
                return False
            xp.long_click()
            return True
        return xp.click_exists(timeout=timeout)

    def tap_contains(
        self, kind: str, substring: str, action_type: str = "click", timeout: float = 5.0
    ) -> bool:
        # a 'label' is the §4 tap union (text ∪ content-desc; hints are field handles,
        # never tap targets) — its decayed-suffix retry searches the same union.
        attrs = {"text": ("@text",), "content_desc": ("@content-desc",),
                 "label": ("@text", "@content-desc")}.get(kind)
        if attrs is None or not substring:
            return False
        lit = f'"{substring}"' if '"' not in substring else f"'{substring}'"
        clause = " or ".join(f"contains({a}, {lit})" for a in attrs)
        xp = self._d.xpath(f"//*[{clause}]")
        if action_type == "long_click":
            if not xp.wait(timeout=timeout):  # bool gate; long_click on the selector
                return False
            xp.long_click()
            return True
        return xp.click_exists(timeout=timeout)

    def set_text(self, selector, value: str) -> None:
        self._d.xpath(selector_to_xpath(selector)).set_text(value)

    def _checked_of(self, el) -> bool:
        info = el.info or {}
        cur = info.get("checked")
        return bool(info.get("selected", False) if cur is None else cur)

    def _read_checked(self, xp) -> Optional[bool]:
        # Live checked-state (falling back to `selected`), or None if the element is absent /
        # vanished — so set_checked treats a missing widget as honest drift, never a crash.
        try:
            el = xp.get(timeout=5.0)
        except Exception:  # noqa: BLE001 — XPathElementNotFoundError / element gone
            return None
        return self._checked_of(el)

    def _read_checked_now(self, xp) -> Optional[bool]:
        # NON-blocking single read (u2 match()) — the poll primitive. A blocking get() here
        # would stall every iteration ~its timeout once the widget vanishes.
        el = xp.match()
        return None if el is None else self._checked_of(el)

    def set_checked(self, selector, target: bool, *, settle_timeout: float = 8.0,
                    interval: float = 0.2, clock=None, sleep=None) -> Optional[bool]:
        # idempotent read-modify-VERIFY, with an ACTIVE WAIT for the flip to land. Read live
        # checked (fall back to selected), tap only on mismatch, then POLL the state until it
        # reaches `target` or `settle_timeout` elapses — returning the INSTANT it flips. So a
        # box that takes 0.1 s and one that takes 8 s (background work) are both handled
        # without a fixed sleep: fast stays fast, slow is tolerated, and a tap that never
        # takes (disabled/gated control) surfaces as drift instead of false success. Replaying
        # twice can't double-flip; a pre-checked launch is left correct.
        #
        # TRI-STATE result (review finding 15): True = flipped/already there; False = honest
        # drift (still present, never reached target); None = the widget VANISHED after the tap
        # (a self-dismissing gate) — the flip is UNVERIFIABLE, which is neither a success claim
        # nor "did not flip". The vanish exits the poll immediately instead of burning the
        # whole settle_timeout on a widget that is no longer there.
        clock = clock or time.monotonic
        sleep = sleep or time.sleep
        xp = self._d.xpath(selector_to_xpath(selector))
        cur = self._read_checked(xp)
        if cur is None:
            return False  # element absent BEFORE any tap -> honest drift (never tap a phantom)
        if cur == bool(target):
            return True  # already in the desired state — Playwright check()'s early return
        xp.click(timeout=5.0)  # tap via the SELECTOR (resolves+taps atomically; the read element
        #                        is used only for state) — one consistent tap path across the driver
        deadline = clock() + settle_timeout
        gone = 0
        while True:
            now = self._read_checked_now(xp)
            if now == bool(target):
                return True
            if now is None:
                gone += 1
                if gone >= 2:  # two consecutive empty reads: vanished, not a transient reflow
                    return None
            else:
                gone = 0
            if clock() >= deadline:
                return False  # still present, never reached the target state -> honest drift
            sleep(interval)

    def _current_ime(self) -> Optional[str]:
        try:
            return self._d.current_ime()
        except Exception:  # noqa: BLE001 — IME query unsupported / device hiccup
            return None

    def _restore_ime(self, original: Optional[str]) -> None:
        # u2's send_keys switches the device's default keyboard to its own AdbKeyboard
        # (com.github.uiautomator) and LEAVES it active. That (a) breaks the user's keyboard and
        # (b) silently breaks the NEXT recording's text capture — the recorder doesn't recognize
        # com.github.uiautomator as an IME, so it never tracks typing. So replay must leave the
        # input method as it found it. Only act if it actually drifted, and validate the id charset
        # before it touches a shell (invariant #4).
        if not original or not _IME_ID_RE.match(original):
            return
        try:
            if self._d.current_ime() != original:
                self._d.shell(f"ime set {original}")
        except Exception:  # noqa: BLE001 — best-effort restore; never crash the replay over it
            pass

    def _send_keys_restoring(self, value: str, *, clear: bool) -> None:
        # commit text through u2's IME pipeline (so onTextChanged/TextWatcher fire — atomic set_text
        # skips that, breaking Compose/search-as-you-type), then restore the user's keyboard.
        original = self._current_ime()
        try:
            self._d.send_keys(value, clear=clear)
        finally:
            self._restore_ime(original)

    def type_text(self, selector, value: str) -> None:
        # focus the field, then commit through u2's IME pipeline. NEVER build a shell string from
        # `value`: it can be a recorded literal or an injected credential, so `shell("input text "
        # + value)` would be a command-injection sink. send_keys routes via the on-device input
        # method — no shell — and we restore the user's keyboard afterward (see _restore_ime).
        if self._d.xpath(selector_to_xpath(selector)).click_exists(timeout=5.0):
            self._send_keys_restoring(value, clear=False)

    def xpath_exists(self, selector) -> bool:
        if selector.kind == "coords":
            return True
        return self._d.xpath(selector_to_xpath(selector)).exists

    def wait_until_present(self, selector, timeout: float = 10.0, *, interval: float = 0.2,
                           clock=None, sleep=None) -> bool:
        # u2's xpath.wait POLLS the live hierarchy internally and returns True the instant the
        # node appears, or False at timeout — a real wait, not a fixed sleep. A coords/keyevent
        # selector has no element to wait on, so the flow proceeds (settle gates it instead).
        # NOTE: .wait() returns a BOOL in u2 v3 — `is not None` made this ALWAYS True (False is
        # not None), silently defeating the honest wait. Return the bool directly.
        if selector.kind in ("coords", "keyevent"):
            return True
        return self._d.xpath(selector_to_xpath(selector)).wait(timeout=timeout)

    def swipe(self, start, end, duration: float = 0.2) -> None:
        (sx, sy), (ex, ey) = start, end
        self._d.swipe(sx, sy, ex, ey, duration)

    def element_center(self, selector):
        if selector.kind == "coords":
            return tuple(selector.value)
        try:
            el = self._d.xpath(selector_to_xpath(selector)).get(timeout=5.0)
        except Exception:  # noqa: BLE001 — element absent / not resolvable
            return None
        if el is None:
            return None
        cx, cy = el.center()  # u2 returns the element's pixel center
        return int(cx), int(cy)

    def focus_and_type(self, selector, value: str, *, clear: bool = True) -> bool:
        # Maestro inputText: FOCUS the field first (tap it so the IME is up and the caret is in
        # it), then commit through the on-device IME so onTextChanged/TextWatcher fire — atomic
        # set_text bypasses that, breaking Compose / search-as-you-type. NEVER build a shell
        # string from `value` (command-injection sink + would log the literal). u2 v3: .wait() is a
        # BOOL presence gate (NOT an element — the original crash was `.click()` on that bool), so
        # focus via the SELECTOR's .click(), and let send_keys clear+type in one IME pass. Honest
        # False if the field never appears.
        xp = self._d.xpath(selector_to_xpath(selector))
        if not xp.wait(timeout=5.0):
            return False
        xp.click(timeout=5.0)
        self._send_keys_restoring(value, clear=clear)  # types, then restores the user's keyboard
        return True

    def verify_text(self, selector, expected: str, *, masked: bool = False, timeout: float = 3.0,
                    interval: float = 0.2, clock=None, sleep=None) -> bool:
        # Confirm the text actually landed — closes the silent set_text no-op. Poll the field's
        # live text until it matches (or, for a masked/password field, until ANY text is present
        # — never compare the secret literal), returning the instant it does.
        clock = clock or time.monotonic
        sleep = sleep or time.sleep
        xp = self._d.xpath(selector_to_xpath(selector))
        deadline = clock() + timeout
        while True:
            # match() is a non-blocking single read of the live hierarchy (element or None). NEVER
            # xp.get(timeout=0.0): u2 reads a falsy timeout as "unset" and blocks ~_global_timeout
            # (~20s) — a hidden blind wait (invariant #3) that also RAISES on a vanished element
            # instead of letting THIS injectable-clock loop own the polling.
            el = xp.match()
            cur = (el.text or "") if el is not None else ""
            if (len(cur) > 0) if masked else (cur == expected):
                return True
            if clock() >= deadline:
                return False
            sleep(interval)

    def wait_activity(self, activity: str, timeout: float = 10.0) -> bool:
        return self._d.wait_activity(activity, timeout=timeout)

    def wait_gone(self, selector, timeout: float = 10.0) -> bool:
        return self._d.xpath(selector_to_xpath(selector)).wait_gone(timeout=timeout)

    def screen_on(self) -> bool:
        return bool(self._d.info.get("screenOn", True))

    def screenshot(self, path: Optional[str] = None) -> bytes:
        if path:
            self._d.screenshot(path)
            return b""
        import io

        buf = io.BytesIO()
        self._d.screenshot().save(buf, format="PNG")
        return buf.getvalue()
