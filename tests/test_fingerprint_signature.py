from wendle.fingerprint.signature import (
    FingerprintConfig,
    fingerprint,
    stabilize,
    structural_signature,
)

# Same structure, different text/bounds + a different status-bar clock value.
SCREEN_A = """<hierarchy>
 <node class="android.widget.FrameLayout" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">
  <node class="android.widget.TextView" resource-id="com.android.systemui:id/clock" clickable="false" content-desc="" text="10:30" bounds="[0,0][100,50]"/>
  <node class="android.widget.Button" resource-id="com.app:id/ok" clickable="true" content-desc="" text="Aceptar" bounds="[40,500][200,560]"/>
 </node>
</hierarchy>"""

SCREEN_B = """<hierarchy>
 <node class="android.widget.FrameLayout" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">
  <node class="android.widget.TextView" resource-id="com.android.systemui:id/clock" clickable="false" content-desc="" text="11:45" bounds="[0,0][100,50]"/>
  <node class="android.widget.Button" resource-id="com.app:id/ok" clickable="true" content-desc="" text="Accept" bounds="[50,510][210,570]"/>
 </node>
</hierarchy>"""


def _list(n):
    rows = "".join(
        f'<node class="android.widget.LinearLayout" resource-id="com.app:id/row" '
        f'clickable="true" content-desc="" text="item {i}" bounds="[0,{i*100}][1080,{i*100+100}]"/>'
        for i in range(n)
    )
    return f'<hierarchy><node class="android.widget.ListView" resource-id="com.app:id/list" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">{rows}</node></hierarchy>'


def test_same_structure_different_text_and_clock_same_signature():
    assert structural_signature(SCREEN_A) == structural_signature(SCREEN_B)


def test_clock_subtree_is_stripped():
    # the only difference between A and B includes the clock value; stripping it
    # (and dropping text) is what makes them equal
    sig = structural_signature(SCREEN_A)
    assert "10:30" not in sig and "clock" not in sig


def test_list_collapse_makes_row_count_irrelevant():
    assert structural_signature(_list(3)) == structural_signature(_list(5))
    assert structural_signature(_list(1)) == structural_signature(_list(9))


def test_different_structure_differs():
    extra = SCREEN_A.replace(
        "</node>\n</hierarchy>",
        '<node class="android.widget.Switch" resource-id="com.app:id/toggle" clickable="true" content-desc="" text="" bounds="[0,600][100,660]"/></node></hierarchy>',
    )
    assert structural_signature(SCREEN_A) != structural_signature(extra)


def test_include_text_knob_changes_signature():
    with_text = '<hierarchy><node class="T" resource-id="" clickable="true" content-desc="" text="hi" bounds="[0,0][1,1]"/></hierarchy>'
    without = '<hierarchy><node class="T" resource-id="" clickable="true" content-desc="" text="" bounds="[0,0][1,1]"/></hierarchy>'
    # default: text dropped -> identical
    assert structural_signature(with_text) == structural_signature(without)
    # include_text: presence differs -> different
    cfg = FingerprintConfig(include_text=True)
    assert structural_signature(with_text, cfg) != structural_signature(without, cfg)


def test_fingerprint_is_namespace_sensitive():
    a = fingerprint("com.app/.A", SCREEN_A)
    b = fingerprint("com.app/.B", SCREEN_A)
    assert a != b  # same screen, different namespace -> different id
    assert a == fingerprint("com.app/.A", SCREEN_B)  # same namespace + same structure


def test_stabilize_reports_equality():
    sig, stable = stabilize(SCREEN_A, SCREEN_B)
    assert stable is True  # structurally equal despite clock/text differences
    sig2, stable2 = stabilize(SCREEN_A, _list(3))
    assert stable2 is False


def _recycler(children):
    inner = "".join(children)
    return f'<hierarchy><node class="androidx.recyclerview.widget.RecyclerView" resource-id="com.app:id/list" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">{inner}</node></hierarchy>'


_ROW = '<node class="android.widget.LinearLayout" resource-id="com.app:id/row" clickable="true" content-desc="" text="r" bounds="[0,0][1080,100]"/>'
_AD = '<node class="android.widget.FrameLayout" resource-id="com.app:id/ad" clickable="true" content-desc="" text="ad" bounds="[0,0][1080,200]"/>'


def test_interleaved_ad_does_not_explode_list_state():
    # rows with ads interleaved at different positions must collapse to the same
    # set of distinct shapes (order- and count-invariant inside a list)
    a = _recycler([_ROW, _AD, _ROW, _ROW, _AD, _ROW])
    b = _recycler([_ROW, _ROW, _AD, _ROW])
    from wendle.fingerprint.signature import structural_signature

    assert structural_signature(a) == structural_signature(b)


def test_denylist_is_exact_entry_name_not_substring():
    from wendle.fingerprint.signature import structural_signature

    # 'clock_in' must NOT be stripped by the 'clock' denylist entry
    xml = '<hierarchy><node class="B" resource-id="com.app:id/clock_in" clickable="true" content-desc="" text="" bounds="[0,0][1,1]"/></hierarchy>'
    assert "clock_in" in structural_signature(xml)


def test_denylisted_container_keeps_its_subtree():
    from wendle.fingerprint.signature import structural_signature

    # a chrome-id'd wrapper is spliced out, but its real labeled child survives
    xml = (
        '<hierarchy><node class="FrameLayout" resource-id="com.android.systemui:id/status_bar" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,80]">'
        '<node class="Button" resource-id="com.app:id/real" clickable="true" content-desc="" text="" bounds="[0,0][100,80]"/>'
        "</node></hierarchy>"
    )
    sig = structural_signature(xml)
    assert "com.app:id/real" in sig
    assert "status_bar" not in sig  # wrapper tuple dropped


def test_list_reorder_of_distinct_siblings_in_nonlist_differs():
    from wendle.fingerprint.signature import structural_signature

    # non-list layout preserves order: A,B != B,A
    def col(order):
        kids = "".join(
            f'<node class="{c}" resource-id="" clickable="true" content-desc="" text="" bounds="[0,0][1,1]"/>'
            for c in order
        )
        return f'<hierarchy><node class="android.widget.LinearLayout" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][10,10]">{kids}</node></hierarchy>'

    assert structural_signature(col("AB")) != structural_signature(col("BA"))


def test_scroll_invariant_list_same_screen_at_any_scroll():
    from wendle.fingerprint.signature import structural_signature

    # heterogeneous Settings-like rows; scrolling reveals DIFFERENT item shapes.
    toggle = '<node class="android.widget.Switch" resource-id="com.app:id/toggle" clickable="true" content-desc="" text="" bounds="[0,0][1080,100]"/>'
    arrow = '<node class="android.widget.LinearLayout" resource-id="com.app:id/row" clickable="true" content-desc="" text="" bounds="[0,0][1080,100]"/>'
    summary = '<node class="android.widget.TextView" resource-id="com.app:id/sum" clickable="true" content-desc="" text="" bounds="[0,0][1080,100]"/>'

    def settings(rows):
        inner = "".join(rows)
        return (
            '<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            '<node class="android.widget.TextView" resource-id="com.app:id/title" clickable="false" content-desc="" text="" bounds="[0,0][1080,80]"/>'
            f'<node class="androidx.recyclerview.widget.RecyclerView" resource-id="com.app:id/list" clickable="false" content-desc="" text="" bounds="[0,80][1080,2340]">{inner}</node>'
            "</node></hierarchy>"
        )

    top = settings([toggle, arrow, summary])
    scrolled = settings([summary, arrow, arrow, toggle, summary])  # different visible items
    assert structural_signature(top) == structural_signature(scrolled)


def test_scroll_invariance_can_be_disabled():
    from wendle.fingerprint.signature import FingerprintConfig, structural_signature

    cfg = FingerprintConfig(scroll_invariant_lists=False)
    a = _recycler([_ROW, _AD])
    b = _recycler([_ROW])
    # with scroll-invariance OFF, distinct content can differ
    assert structural_signature(a, cfg) != structural_signature(b, cfg)
    # with it ON (default), the list is identified by itself -> same
    assert structural_signature(a) == structural_signature(b)


def test_pager_pages_are_ordered_not_dropped():
    from wendle.fingerprint.signature import structural_signature

    def pager(pages):
        inner = "".join(
            f'<node class="android.widget.FrameLayout" resource-id="com.app:id/page_{p}" clickable="true" content-desc="" text="" bounds="[0,0][1080,2000]"/>'
            for p in pages
        )
        return f'<hierarchy><node class="androidx.viewpager.widget.ViewPager" resource-id="com.app:id/pager" clickable="false" content-desc="" text="" bounds="[0,0][1080,2000]">{inner}</node></hierarchy>'

    # different pager pages (distinct fragment subtrees) must NOT collapse
    assert structural_signature(pager(["a", "b"])) != structural_signature(pager(["c", "d"]))


def test_scroll_wrapper_child_contributes_not_dropped():
    from wendle.fingerprint.signature import structural_signature

    def scroller(child_cls):
        return (
            '<hierarchy><node class="android.widget.ScrollView" resource-id="com.app:id/scroll" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2000]">'
            f'<node class="{child_cls}" resource-id="com.app:id/form" clickable="true" content-desc="" text="" bounds="[0,0][1080,2000]"/>'
            "</node></hierarchy>"
        )

    # a ScrollView is a viewport wrapper, not an adapter list -> its child is part of identity
    assert structural_signature(scroller("android.widget.LinearLayout")) != structural_signature(
        scroller("android.widget.RelativeLayout")
    )


def test_ime_classification_is_segment_boundary_not_substring():
    # GAP #2 (completeness audit): _is_ime_pkg keyed on a SUBSTRING, so a legit app package that
    # merely CONTAINS a marker ('com.app.inputmethods') misclassified as an IME and its subtree
    # got pruned from the fingerprint. General-mechanism fix (PRIME DIRECTIVE): IME identity keys
    # on a package COMPONENT, never an arbitrary substring.
    from wendle.fingerprint.signature import is_ime_pkg
    # real soft-keyboard packages still classify (component match holds)
    assert is_ime_pkg("com.google.android.inputmethod.latin")
    assert is_ime_pkg("com.android.inputmethod.pinyin")
    # legit app packages that only CONTAIN a marker substring must NOT be pruned as IME
    assert is_ime_pkg("com.app.inputmethods") is False   # 'inputmethods' is not the 'inputmethod' segment
    assert is_ime_pkg("com.foo.latinamerica") is False   # 'latinamerica' is not the 'latin' segment
    assert is_ime_pkg("com.bar.imexpress") is False      # 'imexpress' is not the 'ime' segment
    # a non-IME keyboard-adjacent package with no marker segment is unchanged
    assert is_ime_pkg("com.samsung.android.honeyboard") is False
