"""§2 identity — adapter regions beyond class names (the lazy-list design contract).

The app-agnostic
rule under test: *a scroll-capable anonymous container whose direct children contain a ≥3-run
of one structural shape is an adapter region; the region's identity is the container plus its
non-repeating children, never the current window of the run.*

Laws pinned here: L1 (identity = pure fn of the dump — no history inputs), L2 (every knob
fails toward NO collapse), run scoping (leading/trailing children stay fully value-hashed).
"""
import pytest

from wendle.fingerprint.signature import (
    NO_COLLAPSE_EXTENT_BAND,
    SIGNATURE_VERSION,
    FingerprintConfig,
    AdapterRegion,
    adapter_region,
    adapter_list_dominant,
    chrome_digest,
    fingerprint,
    has_collapsing_list,
    outside_region_value_bearing,
    structural_signature,
    structure_id,
)

from defusedxml.ElementTree import fromstring

NS = "com.app/.Feed"
# Compose-like profile, constructed directly (text promoted into the hash at shallow depth).
CFG = FingerprintConfig(include_text=True, title_value_max_depth=4)


def _n(cls, *kids, rid="", text="", desc="", clickable="false", scrollable="false",
       bounds="[0,0][1080,200]", pkg="com.app"):
    return (
        f'<node class="{cls}" package="{pkg}" resource-id="{rid}" text="{text}" '
        f'content-desc="{desc}" clickable="{clickable}" checkable="false" focusable="false" '
        f'scrollable="{scrollable}" bounds="{bounds}">' + "".join(kids) + "</node>"
    )


def _h(*tops):
    return ("<hierarchy>"
            + _n("android.widget.FrameLayout", *tops, bounds="[0,0][1080,2340]")
            + "</hierarchy>")


def _row(y, label, h=300):
    """An anonymous feed row: clickable View wrapping a TextView leaf (Compose-shaped)."""
    return _n("android.view.View",
              _n("android.widget.TextView", text=label, bounds=f"[40,{y+10}][1000,{y+90}]"),
              clickable="true", bounds=f"[0,{y}][1080,{y+h}]")


def _feed(rows, header=None, footer=None, scrollable="true", cls="android.view.View",
          cont_bounds="[0,300][1080,2100]"):
    kids = ([] if header is None else [header]) + rows + ([] if footer is None else [footer])
    return _h(_n(cls, *kids, scrollable=scrollable, bounds=cont_bounds))


def _el(xml):
    """First semantic child of the FrameLayout root (the container under test)."""
    root = fromstring(xml)
    frame = next(c for c in root if c.tag == "node")
    return next(c for c in frame if c.tag == "node")


ROWS_A = [_row(300 + i * 300, f"Item {i}") for i in range(4)]
ROWS_B = [_row(300 + i * 300, f"Other {i}") for i in range(6)]  # different window: count AND texts
HEADER = _n("android.widget.TextView", text="Inbox", bounds="[0,300][1080,400]")
HEADER2 = _n("android.widget.TextView", text="Archive", bounds="[0,300][1080,400]")


# ---- detection: the D2 rung ----

def test_d2_fires_on_anonymous_scrollable_run():
    region = adapter_region(_el(_feed(ROWS_A)), CFG)
    assert isinstance(region, AdapterRegion) and region.kind == "run"
    assert (region.start, region.end) == (0, 4)


def test_d1_class_containers_keep_whole_container_kind():
    xml = _h(_n("androidx.recyclerview.widget.RecyclerView", *ROWS_A, scrollable="true",
                bounds="[0,300][1080,2100]"))
    region = adapter_region(_el(xml), CFG)
    assert region is not None and region.kind == "class"


def test_d2_requires_scrollable_in_this_dump():
    # chip strip / static repeated group: no flag -> no region (T6 structural exclusion;
    # screen-level history must grant nothing — identity is a pure fn of THIS dump, L1)
    assert adapter_region(_el(_feed(ROWS_A, scrollable="false")), CFG) is None


def test_d2_requires_anonymous_container_class():
    xml = _feed(ROWS_A, cls="android.widget.LinearLayout")
    assert adapter_region(_el(xml), CFG) is None


def test_d2_requires_three_identical_shapes():
    assert adapter_region(_el(_feed(ROWS_A[:2])), CFG) is None  # n=2 run: refuse (L2)


def test_d2_declines_heterogeneous_children():
    # T14 Flutter static form: anonymous + scrollable + >=3 children but no 3-run
    form = _h(_n("android.view.View",
                 _n("android.widget.TextView", text="Name", bounds="[0,300][1080,400]"),
                 _n("android.widget.EditText", text="", clickable="true", bounds="[0,400][1080,520]"),
                 _n("android.view.View", clickable="true", bounds="[0,520][1080,640]"),
                 _n("android.widget.Button", text="Submit", clickable="true", bounds="[0,640][1080,760]"),
                 scrollable="true", bounds="[0,300][1080,2100]"))
    assert adapter_region(_el(form), CFG) is None


def test_d2_wrapper_chain_transparency():
    # scroll flag on a single-child anonymous wrapper; the run-bearing child is unflagged (T3)
    inner = _n("android.view.View", *ROWS_A, bounds="[0,300][1080,2100]")
    xml = _h(_n("android.view.View", inner, scrollable="true", bounds="[0,300][1080,2100]"))
    wrapper = _el(xml)
    inner_el = next(c for c in wrapper if c.tag == "node")
    assert adapter_region(inner_el, CFG, inherited_scroll=True) is not None
    assert adapter_region(inner_el, CFG) is None  # no flag, no inheritance -> nothing (pure)


def test_extent_band_vetoes_pagers_and_peek_carousels():
    lo, hi = NO_COLLAPSE_EXTENT_BAND
    assert (lo, hi) == (0.70, 1.00)
    # horizontal full-page pager: 3 pages, each ~95% of container width
    pages = [_n("android.view.View",
                _n("android.widget.TextView", text=f"P{i}", bounds=f"[{i*1026+40},400][{i*1026+400},500]"),
                bounds=f"[{i*1026},300][{(i+1)*1026},2100]") for i in range(3)]
    pager = _h(_n("android.view.View", *pages, scrollable="true", bounds="[0,300][1080,2100]"))
    assert adapter_region(_el(pager), CFG) is None
    # vertical full-page feed (each item ~viewport-high): honest ghost-twin posture, no collapse
    tall = [_row(300 + i * 1200, f"V{i}", h=1200) for i in range(3)]  # 1200/1800≈0.67 < band
    assert adapter_region(_el(_feed(tall)), CFG) is not None
    full = [_row(300 + i * 1800, f"V{i}", h=1800) for i in range(3)]  # 1800/1800=1.0: veto
    assert adapter_region(_el(_feed(full, cont_bounds="[0,300][1080,2100]")), CFG) is None


def test_missing_bounds_vetoes_collapse():
    rows = [r.replace('bounds="[0,', 'bounds="[zz,', 1) for r in ROWS_A]  # corrupt child bounds
    xml = _feed(rows)
    assert adapter_region(_el(xml), CFG) is None  # uncertain extent -> NO collapse (L2)


def test_interior_outliers_swallowed_up_to_two():
    ad = _n("android.view.View", desc="Sponsored", bounds="[0,900][1080,1100]")
    rows = ROWS_A[:2] + [ad] + ROWS_A[2:]
    region = adapter_region(_el(_feed(rows)), CFG)
    assert region is not None and (region.start, region.end) == (0, 5)
    # 3 interior non-conformers (mutually DISTINCT shapes — 3 identical ones would
    # legitimately be their own run) -> the run breaks; 2+2 rows never reach a 3-run
    distinct = [
        _n("android.view.View", desc="Ad", bounds="[0,900][1080,1100]"),
        _n("android.widget.TextView", text="Section", bounds="[0,1100][1080,1200]"),
        _n("android.view.View", clickable="true", desc="Banner", bounds="[0,1200][1080,1400]"),
    ]
    broken = ROWS_A[:2] + distinct + ROWS_A[2:]
    assert adapter_region(_el(_feed(broken)), CFG) is None


def test_leading_trailing_children_stay_outside_the_run():
    region = adapter_region(_el(_feed(ROWS_A, header=HEADER)), CFG)
    assert region is not None and (region.start, region.end) == (1, 5)


# ---- emission: scroll-invariance + run scoping in the signatures ----

def test_signature_is_window_invariant_over_the_run():
    s1 = structural_signature(_feed(ROWS_A), CFG)
    s2 = structural_signature(_feed(ROWS_B), CFG)
    assert s1 == s2  # different window (count AND texts) -> same identity
    assert fingerprint(NS, _feed(ROWS_A), CFG) == fingerprint(NS, _feed(ROWS_B), CFG)
    assert structure_id(NS, _feed(ROWS_A)) == structure_id(NS, _feed(ROWS_B))


def test_signature_window_invariant_with_interior_outlier_moved():
    ad = _n("android.view.View", desc="Sponsored", bounds="[0,900][1080,1100]")
    w1 = _feed(ROWS_A[:2] + [ad] + ROWS_A[2:])
    w2 = _feed([ROWS_A[0]] + [ad] + ROWS_A[1:])
    assert structural_signature(w1, CFG) == structural_signature(w2, CFG)


def test_leading_header_keeps_its_value_in_the_hash():
    inbox = _feed(ROWS_A, header=HEADER)
    archive = _feed(ROWS_A, header=HEADER2)
    assert fingerprint(NS, inbox, CFG) != fingerprint(NS, archive, CFG)  # §2.3: no wrong-merge


def test_run_content_never_enters_the_hash():
    relabeled = [_row(300 + i * 300, f"Secret {i}") for i in range(4)]
    assert fingerprint(NS, _feed(ROWS_A, header=HEADER), CFG) == \
        fingerprint(NS, _feed(relabeled, header=HEADER), CFG)


def test_d1_emission_unchanged():
    rec = _h(_n("androidx.recyclerview.widget.RecyclerView", *ROWS_A, scrollable="true",
                rid="com.app:id/list", bounds="[0,300][1080,2100]"))
    sig = structural_signature(rec, CFG)
    assert "[~]" in sig
    # whole-container semantics: children contribute nothing, today's byte shape
    rec2 = _h(_n("androidx.recyclerview.widget.RecyclerView", *ROWS_B, scrollable="true",
                 rid="com.app:id/list", bounds="[0,300][1080,2100]"))
    assert sig == structural_signature(rec2, CFG)


def test_distinct_row_shapes_make_distinct_regions():
    iconic = [_n("android.view.View",
                 _n("android.widget.ImageView", desc=f"pic{i}", bounds=f"[0,{300+i*300}][200,{500+i*300}]"),
                 clickable="true", bounds=f"[0,{300+i*300}][1080,{600+i*300}]") for i in range(4)]
    assert structural_signature(_feed(ROWS_A), CFG) != structural_signature(_feed(iconic), CFG)


# ---- chrome digest routing (run-scoped; leading header still digest-eligible) ----

def test_chrome_digest_excludes_run_values_keeps_header():
    d_inbox = chrome_digest(_feed(ROWS_A, header=HEADER), CFG)
    d_archive = chrome_digest(_feed(ROWS_A, header=HEADER2), CFG)
    assert d_inbox is not None and d_inbox != d_archive  # header splits twins
    d_w1 = chrome_digest(_feed(ROWS_A, header=HEADER), CFG)
    d_w2 = chrome_digest(_feed(ROWS_B, header=HEADER), CFG)
    assert d_w1 == d_w2  # in-run values contribute nothing


def test_chrome_digest_no_digit_stripping():
    # 'Step N of M stays split' — the named regression from the design review (§2.9)
    step1 = _feed(ROWS_A, header=_n("android.widget.TextView", text="Step 1 of 3",
                                    bounds="[0,300][1080,400]"))
    step2 = _feed(ROWS_A, header=_n("android.widget.TextView", text="Step 2 of 3",
                                    bounds="[0,300][1080,400]"))
    assert chrome_digest(step1, CFG) != chrome_digest(step2, CFG)


# ---- recorded identity-class signals flow through the new predicate ----

def test_has_collapsing_list_true_for_d2_regions():
    assert has_collapsing_list(_feed(ROWS_A))
    assert not has_collapsing_list(_feed(ROWS_A, scrollable="false"))


def test_adapter_list_dominant_counts_run_leaves():
    assert adapter_list_dominant(_feed(ROWS_A))
    # header-dominated screen: most leaves OUTSIDE the run
    chrome = [_n("android.widget.TextView", text=f"C{i}", bounds=f"[0,{i*100}][1080,{i*100+90}]")
              for i in range(12)]
    xml = _h(*chrome, _n("android.view.View", *ROWS_A, scrollable="true",
                         bounds="[0,1300][1080,2100]"))
    assert not adapter_list_dominant(xml)


# ---- value-bearing (L3): a fact about the hash, not the profile ----

def test_value_bearing_true_when_outside_region_text_survives():
    assert outside_region_value_bearing(_feed(ROWS_A, header=HEADER), CFG) is True


def test_value_bearing_false_when_all_values_live_in_the_run():
    assert outside_region_value_bearing(_feed(ROWS_A), CFG) is False


def test_value_bearing_false_for_text_free_profiles():
    cfg = FingerprintConfig()  # VIEW profile: no values ever enter the hash
    assert outside_region_value_bearing(_feed(ROWS_A, header=HEADER), cfg) is False


def test_signature_version_bumped():
    assert SIGNATURE_VERSION >= 2
