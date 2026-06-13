from wendle.capture.selectors import synthesize_selector
from wendle.capture.types import UINode


def _node(**kw):
    base = dict(
        cls="android.widget.View",
        resource_id="",
        text="",
        content_desc="",
        clickable=True,
        password=False,
        bounds=(0, 0, 100, 100),
    )
    base.update(kw)
    return UINode(**base)


def test_content_desc_wins_over_text():
    node = _node(content_desc="Submit", text="Log in")
    sel, replay = synthesize_selector(node)
    assert (sel.kind, sel.value) == ("content_desc", "Submit")
    assert replay == "high"


def test_text_used_when_no_content_desc():
    sel, replay = synthesize_selector(_node(text="Log in"))
    assert (sel.kind, sel.value) == ("label", "Log in")
    assert replay == "high"


def test_resource_id_is_medium():
    sel, replay = synthesize_selector(_node(resource_id="com.app:id/login"))
    assert sel.kind == "resource_id"
    assert replay == "medium"


def test_coords_fallback_is_coordinate_only():
    sel, replay = synthesize_selector(_node(), center=(50, 60))
    assert (sel.kind, sel.value) == ("coords", (50, 60))
    assert replay == "coordinate_only"


def test_empty_content_desc_falls_to_text():
    sel, _ = synthesize_selector(_node(content_desc="", text="Next"))
    assert (sel.kind, sel.value) == ("label", "Next")


def test_sensitive_skips_text_and_content_desc():
    node = _node(content_desc="Password", text="secret", resource_id="com.app:id/pw")
    sel, replay = synthesize_selector(node, sensitive=True)
    assert sel.kind == "resource_id"
    assert replay == "medium"


def test_borrow_from_labeled_descendant():
    from wendle.capture.selectors import borrow_descendant_selector

    container = _node(resource_id="", text="", content_desc="", clickable=True, bounds=(0, 0, 200, 100))
    label = _node(text="Settings", clickable=False, bounds=(10, 10, 190, 90))
    result = borrow_descendant_selector(container, [container, label], 100, 50)
    assert result is not None
    sel, replay = result
    assert (sel.kind, sel.value) == ("label", "Settings")
    assert replay == "medium"


def test_borrow_prefers_content_desc_and_skips_password():
    from wendle.capture.selectors import borrow_descendant_selector

    container = _node(clickable=True, bounds=(0, 0, 200, 100))
    secret = _node(text="hunter2", password=True, bounds=(10, 10, 90, 90))
    labeled = _node(content_desc="Account", bounds=(100, 10, 190, 90))
    sel, replay = borrow_descendant_selector(container, [container, secret, labeled], 150, 50)
    assert sel.kind == "content_desc"
    assert sel.value == "Account"


def test_borrow_returns_none_when_no_labeled_descendant():
    from wendle.capture.selectors import borrow_descendant_selector

    container = _node(clickable=True, bounds=(0, 0, 200, 100))
    bare = _node(clickable=True, bounds=(10, 10, 90, 90))
    assert borrow_descendant_selector(container, [container, bare], 50, 50) is None
