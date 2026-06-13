"""Label-union ambiguity gets a TYPED reason (the tracked v1 refinement).

The driver seam's tri-state idiom (set_checked precedent): resolve_and_tap returns
None = refused-AMBIGUOUS (matched >1 distinct element; tapping the first would be the
cardinal sin), False = not found, True = acted. The executor maps None to a typed
AMBIGUOUS_MATCH reason, so callers stop honestly naming the ambiguity instead of a
generic not_resolved — and the reason stays value-free.
"""
import pytest

from wendle import actions
from wendle.driver.fake import FakeDriver
from wendle.models import Action, Selector
from wendle.replay.result import ReplayResult, ReplayStep, StopReason, classify_stop


def test_executor_maps_tristate_none_to_ambiguous_match():
    class AmbiguousDriver(FakeDriver):
        def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
            self.taps.append((selector.kind, selector.value, action_type))
            return None  # union matched >1 distinct element — refused

    drv = AmbiguousDriver()
    res = actions.execute(Action(selector=Selector("label", "Continue"), action_type="click"),
                          actions.ActionContext(drv))
    assert not res.ok and res.reason == actions.AMBIGUOUS_MATCH
    assert "Continue" not in (res.error or "")  # value-free reason


def test_ambiguous_match_does_not_trigger_the_decay_retry():
    # the element IS present (twice) — retrying on the stable prefix would just re-find
    # the same ambiguity (or worse, a third lookalike); refuse immediately instead
    taps = []

    class AmbiguousDriver(FakeDriver):
        def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
            taps.append(("exact", selector.kind))
            return None

        def tap_contains(self, kind, substring, action_type="click", timeout=5.0):
            taps.append(("contains", kind))
            return True

    res = actions.execute(
        Action(selector=Selector("label", "Alice, sent 5 min ago"), action_type="click"),
        actions.ActionContext(AmbiguousDriver()))
    assert not res.ok and res.reason == actions.AMBIGUOUS_MATCH
    assert ("contains", "label") not in taps  # no first-match fallback after a refusal


def test_stop_reason_taxonomy_gains_ambiguous_match():
    info = classify_stop("ambiguous match: label")
    assert info.kind == StopReason.AMBIGUOUS_MATCH


def test_fake_driver_label_ambiguity_is_element_keyed_like_hardware():
    # The hardware semantic (u2 + pick_unique_deepest): refusal keys on >1 DISTINCT
    # ELEMENTS, never on attr count. One element carrying the value in BOTH text and
    # content-desc (the common accessibility shape) is a UNIQUE match and must tap —
    # the earlier attr-counting fake refused exactly what hardware taps (the
    # mock-contract divergence class from project memory).
    one_element_two_attrs = FakeDriver(
        present_selectors={("text", "Continue"), ("content_desc", "Continue")})
    assert one_element_two_attrs.resolve_and_tap(Selector("label", "Continue"), "click") is True
    # true >1-distinct-element ambiguity is modeled EXPLICITLY
    drv = FakeDriver(present_selectors={("text", "Continue")})
    drv.ambiguous_labels.add("Continue")
    assert drv.resolve_and_tap(Selector("label", "Continue"), "click") is None
    drv2 = FakeDriver(present_selectors={("text", "Continue")})
    assert drv2.resolve_and_tap(Selector("label", "Continue"), "click") is True
