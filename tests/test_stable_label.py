"""A decayed label (name + volatile status/timestamp) still resolves on its stable part.

On-device: a chat row recorded as content_desc='Robertín, Enviado hace 53 min' — the
timestamp moves, so the exact selector vanishes. The replay retries on 'Robertín'.
"""
from wendle.driver.fake import FakeDriver
from wendle.models import Action, Selector
from wendle.navigate.navigator import Navigator
from wendle.graph import Graph


def _nav(present):
    return Navigator(Graph(), FakeDriver(present_selectors=present))


def test_exact_label_taps_directly():
    nav = _nav({("content_desc", "Robertín, Enviado hace 53 min")})
    res = nav._execute(Action(
        selector=Selector("content_desc", "Robertín, Enviado hace 53 min"), action_type="click"))
    assert res.ok and res.error is None


def test_decayed_label_falls_back_to_stable_prefix():
    # the exact recorded label is gone; only the contact name persists
    nav = _nav({("content_desc", "Robertín")})  # contains-match backs this
    res = nav._execute(Action(
        selector=Selector("content_desc", "Robertín, Enviado hace 53 min"), action_type="click"))
    assert res.ok and res.error is None
    # it tried exact first, then the contains-fallback on the stable token
    assert any(t[1] == "contains:Robertín" for t in nav.driver.taps)


def test_no_comma_no_fallback_attempt():
    nav = _nav(set())  # nothing present
    res = nav._execute(Action(selector=Selector("text", "Settings"), action_type="click"))
    assert res.ok is False
    assert not any("contains:" in str(t[1]) for t in nav.driver.taps)  # no comma -> no fallback
