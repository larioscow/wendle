"""§4 — selector fidelity on rid/desc-scarce (pure-Compose / anonymous) screens.

Shipped device-free: the `hint` rung in the FIELD ladder (a pure-Compose field's stable
handle is its placeholder), raw-testTag resource-id pass-through (never synthesize or
validate a package prefix), and the same-bounds attribute-donor borrow (a merging Compose
clickable's desc migrates to a synthetic same-bounds child). The `label` union kind is
deliberately NOT here — its deepest-match/uniqueness enforcement needs the u2 element API
verified on hardware first (device-seam lesson: never ship guessed driver shapes).
"""
from wendle.capture.selectors import borrow_descendant_selector, synthesize_selector
from wendle.capture.types import UINode
from wendle.driver.u2_driver import selector_to_xpath
from wendle.models import Selector


def _node(cls="android.widget.EditText", rid="", text="", desc="", hint="",
          clickable=False, password=False, bounds=(0, 100, 1080, 220)):
    return UINode(cls=cls, resource_id=rid, text=text, content_desc=desc,
                  clickable=clickable, password=password, bounds=bounds,
                  hint_text=hint)


# ---- the field ladder: resource-id -> hint -> content-desc -> coords ----

def test_field_binds_to_hint_when_no_resource_id():
    sel, rep = synthesize_selector(_node(hint="Email"), field=True)
    assert (sel.kind, sel.value, rep) == ("hint", "Email", "medium")


def test_field_resource_id_still_wins_over_hint():
    sel, _ = synthesize_selector(_node(rid="com.app:id/email", hint="Email"), field=True)
    assert sel.kind == "resource_id"


def test_sensitive_field_never_binds_to_hint_or_desc():
    # a secret field's visible labels are never baked into its selector
    sel, rep = synthesize_selector(_node(hint="Password", desc="Password", password=True),
                                   field=True, sensitive=True)
    assert sel.kind == "coords" and rep == "coordinate_only"


def test_hint_outranks_content_desc_for_fields():
    sel, _ = synthesize_selector(_node(hint="Search", desc="Search box"), field=True)
    assert (sel.kind, sel.value) == ("hint", "Search")


# ---- xpath plumbing ----

def test_hint_selector_xpath():
    assert selector_to_xpath(Selector("hint", "Email")) == '//*[@hint="Email"]'


def test_raw_testtag_resource_id_passes_verbatim():
    # Compose testTagsAsResourceId yields colon-less ids; no pkg:id/ prefix is synthesized
    assert selector_to_xpath(Selector("resource_id", "loginButton")) == \
        '//*[@resource-id="loginButton"]'
    assert selector_to_xpath(Selector("resource_id", "com.app:id/login")) == \
        '//*[@resource-id="com.app:id/login"]'


# ---- same-bounds attribute donors (Compose emitFakeNodes) ----

def test_same_bounds_fake_child_donates_its_desc():
    # a merging Compose clickable dumps desc-less, with a synthetic SAME-BOUNDS,
    # non-clickable child at index 0 carrying the content-desc
    container = _node(cls="android.view.View", clickable=True, bounds=(0, 500, 1080, 800))
    fake = _node(cls="android.view.View", desc="Open settings", bounds=(0, 500, 1080, 800))
    got = borrow_descendant_selector(container, [container, fake], 540, 650)
    assert got is not None
    sel, rep = got
    assert (sel.kind, sel.value, rep) == ("content_desc", "Open settings", "medium")


def test_pick_unique_deepest_drops_ancestors_and_refuses_lookalikes():
    from wendle.driver.u2_driver import pick_unique_deepest

    class El:
        def __init__(self, parent=None):
            self._p = parent

        def getparent(self):
            return self._p

    class Match:
        def __init__(self, elem):
            self.elem = elem

    root = El()
    leaf = El(parent=root)
    # an ancestor whose text merged upward must not shadow its leaf -> the leaf wins
    got = pick_unique_deepest([Match(root), Match(leaf)])
    assert got is not None and got.elem is leaf
    # two UNRELATED lookalikes -> refuse (None), never first-match
    assert pick_unique_deepest([Match(El()), Match(El())]) is None
    assert pick_unique_deepest([]) is None
    only = Match(El())
    assert pick_unique_deepest([only]) is only


def test_donor_text_preferred_and_password_never_borrowed():
    container = _node(cls="android.view.View", clickable=True, bounds=(0, 500, 1080, 800))
    secret = _node(text="hunter2", password=True, bounds=(0, 500, 1080, 800))
    label = _node(cls="android.widget.TextView", text="Wi-Fi", bounds=(40, 520, 400, 580))
    got = borrow_descendant_selector(container, [container, secret, label], 540, 650)
    sel, _ = got
    assert (sel.kind, sel.value) == ("label", "Wi-Fi")  # the secret was never a candidate
