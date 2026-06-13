"""Device-free contract test for U2Driver against the uiautomator2 3.5.2 API SHAPE.

The driver talks to a real device, so its device-facing methods had NO device-free coverage —
and a u2 API-shape mismatch shipped silently and only crashed on hardware (the Gemini typing
replay: focus_and_type called .click() on a bool). u2 3.5.2's contract:

  selector = d.xpath(xpath_str)            # DeviceXPathSelector
    .wait(timeout)      -> bool            # PRESENCE gate, NOT an element (the Gemini crash was
                                           #   calling .click() on this bool)
    .get(timeout)       -> DeviceXMLElement  # raises XPathElementNotFoundError if absent; a FALSY
                                           #   timeout (0.0/None) is "unset" -> blocks ~20s (footgun)
    .match()            -> DeviceXMLElement | None   # NON-blocking single read (use for polling)
    .click(timeout)     -> taps (raises if absent)         # actions live on the SELECTOR
    .long_click()       -> long-press (no timeout arg)
    .click_exists(t)    -> bool            # tap-if-present
    .set_text(text)     -> sets text
    .exists             -> bool (property)
  element = selector.get(...) / .match()   # DeviceXMLElement
    .info / .text / .bounds  -> properties
    .center()                -> method
    .click() / .long_click() -> DO exist on DeviceXMLElement (only .set_text is absent)

The driver deliberately drives ALL taps through the SELECTOR (.click/.long_click/.click_exists),
never the returned element — one consistent tap path — so MockElement is kept click-less ON PURPOSE
to enforce that discipline (NOT a claim that the real element lacks click). MockU2 otherwise mirrors
the 3.5.2 shape faithfully (wait->bool; get raises on absence and on a falsy timeout; match is the
non-blocking read), so a method that misuses the API fails here device-free, not on the user's phone.
"""
import pytest

from wendle.driver.u2_driver import U2Driver, selector_to_xpath
from wendle.models import Selector


class _NotFound(Exception):
    """Stand-in for u2's XPathElementNotFoundError: .get()/.click()/.set_text() raise when absent."""


class MockElement:
    """Mirrors the READ surface of u2 3.5.2's DeviceXMLElement (.info/.text/.bounds props, .center()
    method). The real DeviceXMLElement also HAS .click()/.long_click() (only .set_text is absent), but
    MockElement omits them ON PURPOSE: the driver must drive every tap through the SELECTOR, not the
    returned element, so a stray el.click() (the original Gemini bug shape) fails loudly here."""

    def __init__(self, info):
        self._info = dict(info)

    @property
    def info(self):
        return dict(self._info)

    @property
    def text(self):
        return self._info.get("text", "") or ""

    @property
    def bounds(self):
        return self._info.get("bounds", (0, 0, 10, 10))

    @property
    def elem(self):
        # u2 3.5.2 DeviceXMLElement exposes the underlying lxml element as .elem (used by
        # the label-union deepest-match dedup via getparent()). Mutually-unrelated mock
        # matches => none is an ancestor => pick_unique_deepest sees them all as survivors.
        return _LxmlElem(self._info.get("parent"))

    def center(self):
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


class _LxmlElem:
    """Minimal lxml-element stand-in: only .getparent() is read by pick_unique_deepest."""

    def __init__(self, parent=None):
        self._parent = parent

    def getparent(self):
        return self._parent


class MockSelector:
    def __init__(self, dev, xpath):
        self._dev = dev
        self._xpath = xpath

    def _info(self):
        return self._dev._elements.get(self._xpath)

    def _apply_click(self):
        # a tap may mutate state (a checkbox flip / a field gaining text) or REMOVE the element
        # (on_click maps to None — a self-dismissing gating checkbox) — drives set_checked tests
        if self._xpath in self._dev._on_click:
            nxt = self._dev._on_click[self._xpath]
            if nxt is None:
                self._dev._elements.pop(self._xpath, None)
            else:
                self._dev._elements[self._xpath] = nxt

    @property
    def exists(self):
        return self._info() is not None

    def wait(self, timeout=None):
        return self._info() is not None  # BOOL — the crux of the original bug

    def wait_gone(self, timeout=None):
        return self._info() is None

    def get(self, timeout=None):
        # Real u2 reads a FALSY timeout (0.0/None) as "unset" and blocks ~_global_timeout (~20s):
        # `if not self.wait(timeout or self._global_timeout)`. That is a hidden blind wait — driver
        # code must pass a real timeout or use match() for a non-blocking read. Make the footgun a
        # loud test failure instead of silently pretending get(0.0) is instant.
        if not timeout:
            raise AssertionError(
                "u2 selector.get(timeout=falsy) blocks ~20s in real u2 (blind wait); "
                "use .match() for a non-blocking read or pass a real timeout"
            )
        info = self._info()
        if info is None:
            raise _NotFound(self._xpath)
        return MockElement(info)

    def match(self):
        # u2 3.5.2: `if self.exists: return self.get_last_match()` — a NON-blocking single read of
        # the live hierarchy, element or None. The correct primitive for an injectable-clock poll.
        info = self._info()
        return MockElement(info) if info is not None else None

    def all(self):
        # u2 3.5.2: DeviceXPathSelector.all() -> list[DeviceXMLElement]. A `_multi` map scripts
        # an xpath -> several matches (the label-union ambiguity case); else the single element.
        multi = self._dev._multi.get(self._xpath)
        if multi is not None:
            return [MockElement(info) for info in multi]
        info = self._info()
        return [MockElement(info)] if info is not None else []

    def click(self, timeout=None):
        if self._info() is None:
            raise _NotFound(self._xpath)
        self._dev.taps.append(self._xpath)
        self._apply_click()

    def long_click(self):
        if self._info() is None:
            raise _NotFound(self._xpath)
        self._dev.long_clicks.append(self._xpath)

    def click_exists(self, timeout=None):
        if self._info() is None:
            return False
        self._dev.taps.append(self._xpath)
        self._apply_click()
        return True

    def set_text(self, text):
        if self._info() is None:
            raise _NotFound(self._xpath)
        self._dev.set_texts.append((self._xpath, text))


# u2's own soft keyboard — send_keys switches the device's default IME to this and leaves it active.
ADB_KEYBOARD = "com.github.uiautomator/.AdbKeyboard"
USER_KEYBOARD = "com.google.android.inputmethod.latin/.LatinIME"


class MockU2:
    """Faithful stand-in for the uiautomator2 Device surface U2Driver touches."""

    def __init__(self, elements=None, on_click=None, info=None, ime=USER_KEYBOARD, multi=None):
        self._elements = dict(elements or {})        # xpath -> info dict (absent if missing)
        self._on_click = dict(on_click or {})        # xpath -> info dict applied after a tap
        self._multi = dict(multi or {})              # xpath -> [info, ...] (label-union all())
        self._info = info or {"displayWidth": 1080, "displayHeight": 2400, "screenOn": True}
        self._ime = ime              # current default IME (what current_ime() reads)
        self.taps = []
        self.long_clicks = []
        self.set_texts = []
        self.sent_keys = []          # (text, clear)
        self.coord_clicks = []
        self.coord_long_clicks = []  # (x, y) of coordinate long-presses
        self.ime_sets = []           # ids the driver restored to via `ime set <id>`

    def xpath(self, xpath):
        return MockSelector(self, xpath)

    def send_keys(self, text, clear=False):
        self.sent_keys.append((text, clear))
        self._ime = ADB_KEYBOARD     # u2 switches to its AdbKeyboard and leaves it active

    def current_ime(self):
        return self._ime

    def shell(self, cmd):
        # the driver restores the keyboard via `ime set <id>`
        if isinstance(cmd, str) and cmd.startswith("ime set "):
            self._ime = cmd[len("ime set "):].strip()
            self.ime_sets.append(self._ime)
        return ("", 0)

    def click(self, x, y):
        self.coord_clicks.append((x, y))

    def long_click(self, x, y, duration=None):
        # u2 3.x coordinate long-press: d.long_click(x, y[, duration]).
        self.coord_long_clicks.append((x, y))

    @property
    def info(self):
        return dict(self._info)


# ---- helpers -------------------------------------------------------------------------------

SEL = Selector("resource_id", "com.app:id/field")
XP = selector_to_xpath(SEL)


def _drv(elements=None, on_click=None):
    """U2Driver wired to a MockU2 whose present elements are keyed by Selector."""
    el = {selector_to_xpath(s): info for s, info in (elements or {}).items()}
    oc = {selector_to_xpath(s): info for s, info in (on_click or {}).items()}
    dev = MockU2(el, oc)
    return U2Driver(device=dev), dev


class _Clock:
    """Injectable monotonic clock + non-sleeping sleep so timeouts run at ~0 wall-time."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


# ---- wait_until_present: the honesty-first bug ---------------------------------------------

def test_wait_until_present_false_when_absent():
    # REGRESSION: was `xp.wait(...) is not None` -> bool is never None -> ALWAYS True, defeating
    # the honest wait (the replay would proceed as if every element appeared).
    d, _ = _drv({})
    assert d.wait_until_present(SEL, timeout=0.5) is False


def test_wait_until_present_true_when_present():
    d, _ = _drv({SEL: {"text": ""}})
    assert d.wait_until_present(SEL, timeout=0.5) is True


def test_wait_until_present_coords_and_keyevent_short_circuit():
    d, _ = _drv({})
    assert d.wait_until_present(Selector("coords", (5, 5))) is True
    assert d.wait_until_present(Selector("keyevent", 4)) is True


# ---- focus_and_type: the on-device crash ---------------------------------------------------

def test_focus_and_type_focuses_then_types_via_ime():
    d, dev = _drv({SEL: {"text": ""}})
    assert d.focus_and_type(SEL, "Hello who is this?") is True
    assert dev.taps == [XP]                                   # focused via SELECTOR tap
    assert dev.sent_keys == [("Hello who is this?", True)]    # IME input, clear=True default


def test_focus_and_type_absent_field_returns_false_and_types_nothing():
    d, dev = _drv({})
    assert d.focus_and_type(SEL, "secret") is False
    assert dev.sent_keys == []                                # never typed into a missing field


def test_focus_and_type_respects_clear_false():
    d, dev = _drv({SEL: {"text": "x"}})
    assert d.focus_and_type(SEL, "more", clear=False) is True
    assert dev.sent_keys == [("more", False)]


def test_focus_and_type_restores_user_keyboard_after_typing():
    # send_keys switches the device to u2's AdbKeyboard; replay MUST restore the user's keyboard,
    # or it leaves typing broken AND silently kills the next recording's text capture (the
    # recorder doesn't recognize com.github.uiautomator as an IME).
    d, dev = _drv({SEL: {"text": ""}})
    assert dev.current_ime() == USER_KEYBOARD
    assert d.focus_and_type(SEL, "hello") is True
    assert dev.current_ime() == USER_KEYBOARD          # restored, not left on AdbKeyboard
    assert dev.ime_sets[-1] == USER_KEYBOARD           # explicit `ime set <user keyboard>` happened


def test_type_text_also_restores_user_keyboard():
    d, dev = _drv({SEL: {}})
    d.type_text(SEL, "abc")
    assert dev.sent_keys == [("abc", False)]
    assert dev.current_ime() == USER_KEYBOARD


def test_ime_restore_skipped_when_not_drifted():
    # if send_keys did NOT switch the IME (fallback path), don't issue a needless `ime set`.
    d, dev = _drv({SEL: {"text": ""}})
    dev.send_keys = lambda text, clear=False: dev.sent_keys.append((text, clear))  # no IME switch
    assert d.focus_and_type(SEL, "hi") is True
    assert dev.ime_sets == []                           # nothing drifted -> no restore command


def test_ime_restore_rejects_unsafe_id_no_shell_injection():
    # an IME id that isn't the strict package/.Class charset must NEVER reach the shell.
    d, dev = _drv({SEL: {"text": ""}})
    dev._ime = "evil; rm -rf /"                         # not a valid IME id
    assert d.focus_and_type(SEL, "hi") is True
    assert dev.ime_sets == []                           # refused — charset-validated before shell


# ---- long-press tap path -------------------------------------------------------------------

def test_resolve_and_tap_long_click_present():
    d, dev = _drv({SEL: {}})
    assert d.resolve_and_tap(SEL, "long_click") is True
    assert dev.long_clicks == [XP]


def test_resolve_and_tap_long_click_absent_is_honest_false():
    d, dev = _drv({})
    assert d.resolve_and_tap(SEL, "long_click") is False
    assert dev.long_clicks == []


def test_resolve_and_tap_click_present_and_absent():
    d, _ = _drv({SEL: {}})
    assert d.resolve_and_tap(SEL) is True
    d2, _ = _drv({})
    assert d2.resolve_and_tap(SEL) is False


def test_resolve_and_tap_coords_taps_pixel():
    d, dev = _drv({})
    assert d.resolve_and_tap(Selector("coords", (40, 90))) is True
    assert dev.coord_clicks == [(40, 90)]


def test_resolve_and_tap_coords_honors_long_click():
    # the reveal rung bounds-anchors a revealed long_click as a coords action; it must
    # issue a real long-press, not silently degrade to a short click (review finding).
    d, dev = _drv({})
    assert d.resolve_and_tap(Selector("coords", (40, 90)), "long_click") is True
    assert dev.coord_long_clicks == [(40, 90)] and dev.coord_clicks == []


def test_resolve_and_tap_label_unique_taps():
    sel = Selector("label", "Continue")
    xp = selector_to_xpath(sel)
    dev = MockU2(elements={xp: {"bounds": (0, 0, 100, 100)}})
    d = U2Driver(device=dev)
    assert d.resolve_and_tap(sel, "click") is True
    assert dev.coord_clicks == [(50, 50)]  # tapped the unique match's center


def test_resolve_and_tap_label_ambiguous_returns_tristate_none():
    # the §4 union matched TWO distinct, mutually-unrelated elements: the driver must REFUSE
    # (tri-state None), never tap the first — the engine then stops typed (ambiguous_match).
    sel = Selector("label", "Continue")
    xp = selector_to_xpath(sel)
    dev = MockU2(
        elements={xp: {"bounds": (0, 0, 100, 100)}},  # .wait() sees presence
        multi={xp: [{"bounds": (0, 0, 100, 100), "parent": None},
                    {"bounds": (0, 200, 100, 300), "parent": None}]},  # two unrelated matches
    )
    d = U2Driver(device=dev)
    assert d.resolve_and_tap(sel, "click") is None
    assert dev.coord_clicks == []  # NOTHING tapped — the cardinal-sin first-match avoided


def test_tap_contains_long_click_present():
    dev = MockU2({'//*[contains(@text, "Sign")]': {}})
    d = U2Driver(device=dev)
    assert d.tap_contains("text", "Sign", "long_click") is True
    assert dev.long_clicks == ['//*[contains(@text, "Sign")]']


# ---- set_checked: idempotent flip with active wait -----------------------------------------

def test_set_checked_already_in_target_no_tap():
    d, dev = _drv({SEL: {"checked": True}})
    assert d.set_checked(SEL, True) is True
    assert dev.taps == []                                     # idempotent early-return, no tap


def test_set_checked_flips_on_mismatch_then_confirms():
    # off -> tapping it lands checked=True; set_checked must tap once, then verify the flip.
    d, dev = _drv({SEL: {"checked": False}}, on_click={SEL: {"checked": True}})
    assert d.set_checked(SEL, True) is True
    assert dev.taps == [XP]


def test_set_checked_drift_when_flip_never_lands():
    # tap does nothing (disabled/gated control): must return False (honest drift), not hang/crash.
    clk = _Clock()
    d, dev = _drv({SEL: {"checked": False}})                  # no on_click -> stays False
    assert d.set_checked(SEL, True, settle_timeout=1.0, clock=clk.time, sleep=clk.sleep) is False


def test_set_checked_absent_element_is_drift_not_crash():
    d, _ = _drv({})
    assert d.set_checked(SEL, True, settle_timeout=0.5, clock=_Clock().time, sleep=_Clock().sleep) is False


def test_set_checked_vanished_after_tap_is_typed_none_not_drift():
    # a self-dismissing gating checkbox: the tap REMOVES the widget. The poll must exit with the
    # typed None (flip UNVERIFIABLE) via non-blocking reads — not spin to settle_timeout (each
    # iteration blocking ~5s in xp.get) and then report a confident-wrong "did not flip" False
    # (review finding 15).
    clk = _Clock()
    d, dev = _drv({SEL: {"checked": False}}, on_click={SEL: None})
    out = d.set_checked(SEL, True, settle_timeout=8.0, clock=clk.time, sleep=clk.sleep)
    assert out is None
    assert dev.taps == [XP]   # the tap WAS issued
    assert clk.t < 8.0        # exited on the vanish, not at the drift deadline


def test_set_checked_uses_selected_when_checked_absent():
    # segmented/tab widget exposes `selected`, not `checked`.
    d, dev = _drv({SEL: {"selected": False}}, on_click={SEL: {"selected": True}})
    assert d.set_checked(SEL, True) is True
    assert dev.taps == [XP]


# ---- verify_text ---------------------------------------------------------------------------

def test_verify_text_match():
    d, _ = _drv({SEL: {"text": "alice"}})
    assert d.verify_text(SEL, "alice") is True


def test_verify_text_mismatch_times_out_false():
    clk = _Clock()
    d, _ = _drv({SEL: {"text": "bob"}})
    assert d.verify_text(SEL, "alice", timeout=1.0, clock=clk.time, sleep=clk.sleep) is False


def test_verify_text_masked_any_text_passes_without_comparing_literal():
    d, _ = _drv({SEL: {"text": "••••"}})   # bullets — never compare the secret
    assert d.verify_text(SEL, "hunter2", masked=True) is True


def test_verify_text_absent_field_times_out_false():
    clk = _Clock()
    d, _ = _drv({})
    assert d.verify_text(SEL, "alice", timeout=0.5, clock=clk.time, sleep=clk.sleep) is False


def test_verify_text_reads_via_nonblocking_match_not_blind_get():
    # Guards invariant #3 (no blind sleeps): if verify_text regressed to xp.get(timeout=0.0),
    # MockSelector.get would raise AssertionError (it models real u2's hidden ~20s poll). Passing
    # here proves verify_text reads through the non-blocking match().
    d, _ = _drv({SEL: {"text": "ok"}})
    assert d.verify_text(SEL, "ok") is True


# ---- element_center ------------------------------------------------------------------------

def test_element_center_present_returns_pixel_center():
    d, _ = _drv({SEL: {"bounds": (0, 100, 200, 300)}})
    assert d.element_center(SEL) == (100, 200)


def test_element_center_absent_returns_none():
    d, _ = _drv({})
    assert d.element_center(SEL) is None


def test_element_center_coords_passthrough():
    d, _ = _drv({})
    assert d.element_center(Selector("coords", (7, 8))) == (7, 8)


# ---- xpath_exists --------------------------------------------------------------------------

def test_xpath_exists_reflects_presence():
    d, _ = _drv({SEL: {}})
    assert d.xpath_exists(SEL) is True
    d2, _ = _drv({})
    assert d2.xpath_exists(SEL) is False


# ---- app_start: the launch ladder's physics (adversarial-review MEDIUM) ----------------------
# FakeDriver.app_start_raises models "a refused `am start -n` raises" and the ladder's
# raise-=-skip contract depends on it — but u2 3.5.2's own app_start DISCARDS the am output, so
# the real driver never delivered that signal (a non-exported recorded activity silently no-ops
# and burns the gate's whole window). U2Driver therefore drives explicit-component starts through
# its own `am start -n` + refusal parse, and honors the base contract that package-default
# (resolved LAUNCHER entry) is DISTINCT from monkey.


class _AmMock(MockU2):
    def __init__(self, shell_outputs=None, **kw):
        super().__init__(**kw)
        self.shell_outputs = dict(shell_outputs or {})
        self.shell_cmds = []
        self.app_stops = []
        self.monkey_app_starts = []

    def shell(self, cmd):
        self.shell_cmds.append(cmd)
        if isinstance(cmd, str) and cmd in self.shell_outputs:
            return (self.shell_outputs[cmd], 0)
        return super().shell(cmd)

    def app_stop(self, package):
        self.app_stops.append(package)

    def app_start(self, package):
        self.monkey_app_starts.append(package)  # u2's own path == monkey LAUNCHER launch


def test_app_start_with_activity_raises_on_refused_component():
    m = _AmMock({"am start -n mx.app/.Welcome":
                 "Starting: Intent { cmp=mx.app/.Welcome }\n"
                 "java.lang.SecurityException: Permission Denial: starting Intent { ... } "
                 "not exported from uid 10123"})
    d = U2Driver(device=m)
    with pytest.raises(RuntimeError):
        d.app_start("mx.app", ".Welcome", stop=True)
    assert m.app_stops == ["mx.app"]  # the stop still happened (mirrors u2 stop=True ordering)


def test_app_start_with_activity_succeeds_silently_on_clean_output():
    m = _AmMock({"am start -n mx.app/.Main": "Starting: Intent { cmp=mx.app/.Main }"})
    d = U2Driver(device=m)
    d.app_start("mx.app", ".Main", stop=True)
    assert "am start -n mx.app/.Main" in m.shell_cmds
    assert m.app_stops == ["mx.app"]
    assert m.monkey_app_starts == []  # never fell back to monkey for an explicit component


def test_app_start_package_default_resolves_the_launcher_entry_not_monkey():
    m = _AmMock({"cmd package resolve-activity --brief mx.app": "priority=0\nmx.app/.RealEntry",
                 "am start -n mx.app/.RealEntry": "Starting: Intent { cmp=mx.app/.RealEntry }"})
    d = U2Driver(device=m)
    d.app_start("mx.app", None, stop=True)
    assert "am start -n mx.app/.RealEntry" in m.shell_cmds  # the DISTINCT package-default path
    assert m.monkey_app_starts == []                        # monkey stays its own rung


def test_app_start_package_default_falls_back_to_u2_when_unresolvable():
    m = _AmMock({"cmd package resolve-activity --brief mx.app": "No activity found"})
    d = U2Driver(device=m)
    d.app_start("mx.app", None)
    assert m.monkey_app_starts == ["mx.app"]  # graceful fallback: u2's monkey LAUNCHER launch


def test_app_start_rejects_shell_metacharacters():
    d = U2Driver(device=_AmMock())
    with pytest.raises(ValueError):
        d.app_start("mx.app; rm -rf /", ".Main")
    with pytest.raises(ValueError):
        d.app_start("mx.app", ".Main; reboot")
