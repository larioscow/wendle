"""A row-tap must borrow the row's LABEL, never a nested independent control.

On-device regression: tapping the Wi-Fi *row* (to open Wi-Fi settings) bound to the
nested Wi-Fi *toggle* (content_desc='Wi-Fi'), so replay toggled Wi-Fi instead of opening
it. The borrowed selector must reproduce the row tap, not a child control's own action.
"""
from wendle.capture.selectors import borrow_descendant_selector
from wendle.capture.types import UINode


def _node(cls, text="", desc="", clickable=False, bounds=(0, 0, 0, 0), rid=""):
    return UINode(cls=cls, resource_id=rid, text=text, content_desc=desc,
                  clickable=clickable, password=False, bounds=bounds)


def _wifi_row():
    row = _node("android.widget.LinearLayout", clickable=True, bounds=(37, 395, 1403, 657))
    label = _node("android.widget.TextView", text="Wi-Fi", bounds=(105, 448, 293, 536))
    switch = _node("android.widget.Switch", desc="Wi-Fi", clickable=True, bounds=(1151, 395, 1320, 657))
    return row, label, switch


def test_borrow_skips_nested_clickable_control():
    row, label, switch = _wifi_row()
    # tap the row between the label and the switch (not on either)
    sel, replay = borrow_descendant_selector(row, [row, label, switch], 700, 526)
    # the row label 'Wi-Fi', NOT the switch desc — AND narrowed to exact @text, because the
    # §4 `label` UNION would also match the switch's content-desc='Wi-Fi' (the on-device
    # AMBIGUOUS_MATCH finding). exact @text hits only the TextView row label.
    assert sel.kind == "text" and sel.value == "Wi-Fi"


def test_borrow_label_when_tap_is_over_the_switch_area_still_avoids_switch():
    # even if the tap fell in the switch's x-band but node_at gave us the ROW (e.g. padding),
    # borrowing must not resolve to the switch's own action.
    row, label, switch = _wifi_row()
    sel, _ = borrow_descendant_selector(row, [row, label, switch], 1200, 540)
    assert not (sel.kind == "content_desc" and sel.value == "Wi-Fi")


def test_borrow_falls_back_to_clickable_child_when_no_plain_label():
    # a container whose only labeled descendant is itself clickable -> still usable
    row = _node("android.widget.FrameLayout", clickable=True, bounds=(0, 0, 1000, 200))
    only = _node("android.widget.Button", text="Go", clickable=True, bounds=(10, 10, 990, 190))
    sel, _ = borrow_descendant_selector(row, [row, only], 500, 100)
    assert sel.value == "Go"


def test_borrow_prefers_label_under_the_tap_point():
    row = _node("android.widget.LinearLayout", clickable=True, bounds=(0, 0, 1000, 300))
    a = _node("android.widget.TextView", text="Top", bounds=(10, 10, 300, 90))
    b = _node("android.widget.TextView", text="Bottom", bounds=(10, 110, 300, 190))
    sel, _ = borrow_descendant_selector(row, [row, a, b], 150, 150)  # over "Bottom"
    assert sel.value == "Bottom"
