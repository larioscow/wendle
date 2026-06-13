"""Selector synthesis must yield a UNIQUE selector on the capture frame (hardware finding).

On the S23 Settings → Connections screen, a Wi-Fi ROW (TextView text='Wi-Fi') sits beside a
Wi-Fi SWITCH (Switch content-desc='Wi-Fi'). Binding the row as the §4 `label` UNION
(text ∪ content-desc ∪ hint) makes it match BOTH widgets, so replay honestly refuses
(AMBIGUOUS_MATCH) — correct, but a flow-breaker where the narrower exact-`text` selector hits
only the row. The general rule (the layer that owns synthesis): prefer the NARROWEST selector
that uniquely identifies the tapped element; widen to the union only when it stays unique.
"""
from wendle.capture.selectors import synthesize_selector
from wendle.capture.types import UINode


def _node(**kw):
    base = dict(resource_id="", text="", content_desc="", cls="android.widget.TextView",
                password=False, clickable=True, bounds=(0, 0, 10, 10))
    base.update(kw)
    return UINode(**base)


def test_label_union_narrows_to_exact_text_when_union_is_ambiguous():
    # the Wi-Fi case: a row (text) and a switch (content-desc) carry the same value
    row = _node(text="Wi-Fi", bounds=(100, 450, 300, 540))
    switch = _node(text="", content_desc="Wi-Fi", bounds=(1150, 395, 1320, 605))
    frame = [row, switch]
    sel, rep = synthesize_selector(row, center=(200, 495), frame_nodes=frame)
    assert sel.kind == "text" and sel.value == "Wi-Fi"  # narrowed — matches ONLY the row
    assert rep == "high"


def test_label_union_kept_when_it_stays_unique():
    # no competing content-desc/hint carrier -> the robust union is preserved (forward-only §4)
    row = _node(text="Continue", bounds=(40, 500, 1040, 620))
    frame = [row, _node(text="Cancel", bounds=(40, 700, 1040, 820))]
    sel, _ = synthesize_selector(row, center=(540, 560), frame_nodes=frame)
    assert sel.kind == "label" and sel.value == "Continue"


def test_no_frame_keeps_todays_union_behavior():
    # back-compat: callers that don't pass the frame keep the forward-only union
    row = _node(text="Wi-Fi")
    sel, _ = synthesize_selector(row, center=(5, 5))
    assert sel.kind == "label"


def test_falls_through_to_resource_id_when_even_exact_text_is_ambiguous():
    # two identical text rows, one carrying a stable resource-id -> the union AND exact text are
    # both ambiguous; bind the resource-id rather than an ambiguous text
    tapped = _node(text="Item", resource_id="com.x:id/row0", bounds=(0, 0, 100, 50))
    twin = _node(text="Item", bounds=(0, 100, 100, 150))
    sel, rep = synthesize_selector(tapped, center=(50, 25), frame_nodes=[tapped, twin])
    assert sel.kind == "resource_id" and sel.value == "com.x:id/row0"
    assert rep == "medium"


def test_content_desc_narrowed_when_ambiguous_and_text_unique():
    # symmetric case: the tapped node has BOTH a (shared) content-desc and a (unique) text.
    # content_desc 'Tab' is ambiguous, so synthesis skips it and binds by the tapped node's
    # text 'Photos' — the union over 'Photos' is itself unique here, so the robust `label`
    # union is kept (narrowing to exact `text` only happens when the UNION is ambiguous).
    tapped = _node(text="Photos", content_desc="Tab", bounds=(0, 0, 100, 50))
    other = _node(text="", content_desc="Tab", bounds=(200, 0, 300, 50))
    sel, _ = synthesize_selector(tapped, center=(50, 25), frame_nodes=[tapped, other])
    assert sel.value == "Photos" and sel.kind in ("label", "text")  # uniquely hits the tapped node


def test_content_desc_narrows_to_exact_text_when_union_also_collides():
    # content-desc 'Wi-Fi' on the tapped node, a SIBLING text 'Wi-Fi' on another node: the
    # union over 'Wi-Fi' is ambiguous (both), but exact @text is unique to the sibling and
    # exact @content-desc unique to the tapped — bind the narrower exact attr that hits THIS node
    tapped = _node(text="", content_desc="Wi-Fi", bounds=(1150, 395, 1320, 605))
    sibling = _node(text="Wi-Fi", content_desc="", bounds=(100, 450, 300, 540))
    sel, _ = synthesize_selector(tapped, center=(1235, 500), frame_nodes=[tapped, sibling])
    assert sel.kind == "content_desc" and sel.value == "Wi-Fi"  # exact attr unique to the switch


def test_tap_label_union_never_binds_a_hint(monkeypatch):
    # S23 confident-wrong sighting: Samsung Settings' search plate ROTATES hint suggestions
    # through setting names. A recorded row tap ('Pantalla de bloqueo y AOD') replayed while
    # the row was below the fold resolved via the search box's HINT and tapped the search box
    # (ok=True -> SearchActivity). General rule: a TAP-class label binds text/content-desc
    # ONLY — an input's hint is a FIELD handle (the separate `hint` kind), never a tap target.
    from wendle.driver.u2_driver import selector_to_xpath
    from wendle.models import Selector
    xp = selector_to_xpath(Selector("label", "Pantalla de bloqueo y AOD"))
    assert "@hint" not in xp, f"tap union must not reach hints: {xp}"
    assert "@text" in xp and "@content-desc" in xp
    # the FIELD kind keeps its hint reach (set_text path)
    assert "@hint" in selector_to_xpath(Selector("hint", "Buscar"))


def test_fake_driver_tap_union_mirrors_no_hint():
    from wendle.driver.fake import FakeDriver
    from wendle.models import Selector
    drv = FakeDriver(present_selectors={("hint", "Pantalla de bloqueo y AOD")})
    # only a hint carrier present: a tap-class label must NOT resolve (False, not a tap)
    assert drv.resolve_and_tap(Selector("label", "Pantalla de bloqueo y AOD"), "click") is False


def test_reveal_union_matching_mirrors_no_hint():
    from wendle.reveal import _SELECTOR_ATTR
    assert "hint" not in _SELECTOR_ATTR["label"]
    assert _SELECTOR_ATTR["hint"] == ("hint",)  # the field kind is untouched
