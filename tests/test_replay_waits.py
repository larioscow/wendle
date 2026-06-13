"""Phase A — the anti-sleep wait primitive. A real wait POLLS and exits the instant the
element appears; it must be injectable (clock/sleep) so device-free tests run at ~0 wall-time.
"""
import time

from wendle.driver.fake import FakeDriver
from wendle.models import Selector


def _vclock():
    """A virtual clock + sleep: sleep advances the clock instead of blocking real time."""
    t = [0.0]
    return (lambda: t[0]), (lambda dt: t.__setitem__(0, t[0] + dt)), t


def test_wait_until_present_returns_when_element_appears_no_real_sleep():
    clock, sleep, t = _vclock()
    drv = FakeDriver()
    drv.present_after[("text", "Ingresar")] = 3  # appears only after 3 polls
    started = time.monotonic()
    ok = drv.wait_until_present(Selector("text", "Ingresar"), timeout=10.0, clock=clock, sleep=sleep)
    assert ok is True
    assert time.monotonic() - started < 0.5  # did NOT block on a real sleep
    assert t[0] > 0  # it DID poll/wait (virtual time advanced) — not a blind no-op


def test_wait_until_present_false_on_timeout():
    clock, sleep, _ = _vclock()
    ok = FakeDriver().wait_until_present(Selector("text", "never"), timeout=2.0, clock=clock, sleep=sleep)
    assert ok is False


def test_wait_until_present_immediate_for_known_selector():
    drv = FakeDriver(present_selectors={("text", "Go")})
    assert drv.wait_until_present(Selector("text", "Go"), timeout=1.0) is True


def test_wait_until_present_coords_always_true():
    # a coordinate tap has no element to wait on — never blocks the flow
    assert FakeDriver().wait_until_present(Selector("coords", (5, 5)), timeout=1.0) is True


# ---- A2: focus-then-type-then-verify (fixes the on-device text failure + silent no-op) ----

def test_focus_and_type_records_focus_then_verify():
    drv = FakeDriver(present_selectors={("resource_id", "user")})
    sel = Selector("resource_id", "user")
    assert drv.focus_and_type(sel, "alice") is True
    assert drv.text_sets[-1] == ("resource_id", "user", "alice", "focus_and_type")
    assert drv.verify_text(sel, "alice") is True  # text landed


def test_verify_text_mismatch_is_honest_false():
    drv = FakeDriver()
    sel = Selector("resource_id", "user")
    drv.focus_and_type(sel, "alice")
    drv.verify_fail.add(("resource_id", "user"))  # simulate the text not landing
    assert drv.verify_text(sel, "alice") is False


def test_verify_text_masked_checks_presence_not_secret():
    drv = FakeDriver()
    sel = Selector("resource_id", "pwd")
    drv.focus_and_type(sel, "hunter2")
    assert drv.verify_text(sel, "WRONG", masked=True) is True  # presence, never the literal
    drv._field_text[("resource_id", "pwd")] = ""  # nothing actually entered
    assert drv.verify_text(sel, "WRONG", masked=True) is False
