from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import groupby
from typing import List, Optional, Tuple

# defusedxml hardens against XXE / billion-laughs (device-sourced XML).
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _xml_fromstring

MALFORMED = "<malformed>"  # sentinel for empty/truncated dumps; caller treats as unsettled

# Bumped whenever the identity function changes meaning (lazy-region change-set, design doc
# 2026-06-09-compose-lazy-design-input.md §2.6). Graphs recorded under an older version are
# REFUSED with a typed re-record message — never a silent app-wide off_graph storm.
SIGNATURE_VERSION = 2

# Volatile-chrome resource-ids as (defining-package prefix, entry-name) rules, matched
# on the resource-id ("pkg:id/entry"). System-scoped so an APP's clock/date/battery
# container survives. Most status-bar churn is handled more broadly by the SystemUI
# overlay strip (by package, focus-gated) in _should_prune; these are belt-and-suspenders.
DenylistRule = Tuple[str, str]
DEFAULT_VOLATILE_RULES: Tuple[DenylistRule, ...] = (
    ("com.android.systemui", "clock"),
    ("com.android.systemui", "clock_view"),
    ("com.android.systemui", "battery"),
    ("com.android.systemui", "status_bar"),
    ("com.android.systemui", "statusIcons"),
    ("com.android.systemui", "carrier"),
    ("com.android.systemui", "carrier_text"),
    ("com.android.systemui", "date"),
    ("com.android.systemui", "keyguard_clock"),
    ("com.android.systemui", "keyguard_status"),
)

# Adapter lists: content-driven -> scroll-invariant collapse (id'd by themselves, §7).
ADAPTER_LIST_CLASSES: Tuple[str, ...] = ("RecyclerView", "ListView", "GridView")
# Pagers: ordered pages — hashed in order, NEVER scroll-dropped (tabs are distinct).
PAGER_CLASSES: Tuple[str, ...] = ("ViewPager", "ViewPager2")
# Scroll wrappers: a viewport around (usually) one child — ordered, NOT a list.
SCROLL_WRAPPER_CLASSES: Tuple[str, ...] = ("ScrollView", "NestedScrollView")
# Standard Android widgets whose text/content-desc VALUE is intrinsically time-varying (a
# playback position, a running clock, a live progress %). Their structure is stable so a screen
# bearing one still SETTLES, but the VALUE churns between settled dumps — it must be kept out of
# the chrome digest (else a media/player screen falsely collides with itself on every revisit and
# mints ghost twins). A bounded, documented taxonomy like the adapter-list classes, NOT per-app.
VOLATILE_WIDGET_CLASSES: Tuple[str, ...] = (
    "SeekBar", "ProgressBar", "Chronometer", "TextClock", "AnalogClock", "DigitalClock", "RatingBar",
)
# Soft-keyboard / IME packages — a separate window overlaid on the screen. A package is an IME
# when one of its dotted/slashed COMPONENTS is a marker — a SEGMENT match, never a raw substring:
# 'com.app.inputmethods' / '.latinamerica' / '.imexpress' merely CONTAIN a marker and are NOT
# keyboards (a substring match silently pruned such an app's own text capture; mirrors the same
# segment-boundary rule is_ime_class uses for the framework's widget classes).
IME_PKG_SEGMENTS: frozenset = frozenset({"inputmethod", "latin", "ime"})
SYSTEMUI_PKG = "com.android.systemui"

# OEM home packages that don't contain the word "launcher".
_KNOWN_LAUNCHER_PKGS = frozenset(
    {"com.miui.home", "com.android.launcher3", "com.oneplus.launcher", "com.realme.launcher"}
)


def is_launcher_namespace(namespace: str) -> bool:
    """True for the default-home / launcher (keyed on PACKAGE, not activity name, so
    an app's own HomeActivity is not mistaken for the launcher).

    DELIBERATE substring (not a segment match, unlike _is_ime_pkg): launcher packages embed
    'launcher' INSIDE a component — `com.google.android.apps.nexuslauncher` (Pixel),
    `com.teslacoilsw.launcher` (Nova) — so a segment-exact rule would REGRESS the real Pixel
    launcher. The launcher vocabulary genuinely uses 'launcher' as an embeddable token; the
    explicit allowlist covers OEM homes that omit the word ('com.miui.home'). The theoretical
    false positive (a non-launcher app whose package contains 'launcher') is accepted over a
    real regression — the right granularity for THIS taxonomy is the substring + allowlist."""
    pkg = (namespace or "").split("/", 1)[0].lower()
    return "launcher" in pkg or pkg in _KNOWN_LAUNCHER_PKGS


@dataclass
class FingerprintConfig:
    """Per-namespace knobs for the structural signature (§7)."""

    include_text: bool = False  # add text-presence to each node tuple
    include_content_desc_values: bool = False
    list_collapse: bool = True
    max_depth: int = 50
    title_value_max_depth: int = 0
    scroll_invariant_lists: bool = True
    resource_id_denylist: Tuple[DenylistRule, ...] = DEFAULT_VOLATILE_RULES
    adapter_list_classes: Tuple[str, ...] = ADAPTER_LIST_CLASSES
    pager_classes: Tuple[str, ...] = PAGER_CLASSES
    scroll_wrapper_classes: Tuple[str, ...] = SCROLL_WRAPPER_CLASSES
    volatile_widget_classes: Tuple[str, ...] = VOLATILE_WIDGET_CLASSES
    strip_ime: bool = True
    strip_overlay_systemui: bool = True
    # When False, the max_depth cap emits the node tuple only (no descendant
    # summary) — a SHALLOW skeleton. Used for inherently-dynamic home/launcher
    # surfaces so swipe-pages / widgets / icon jitter don't fork the screen.
    shallow_summary: bool = True


def _rid_parts(rid: str) -> Tuple[str, str]:
    pkg = rid.split(":", 1)[0] if ":" in rid else ""
    entry = rid.rsplit("/", 1)[1] if "/" in rid else rid
    return pkg, entry


def _denylisted(rid: str, config: FingerprintConfig) -> bool:
    if not rid:
        return False
    pkg, entry = _rid_parts(rid)
    return any(
        entry == rule_entry and (rule_pkg == "" or pkg.startswith(rule_pkg))
        for rule_pkg, rule_entry in config.resource_id_denylist
    )


def _is_ime_pkg(pkg: str) -> bool:
    return bool(pkg) and any(seg in IME_PKG_SEGMENTS for seg in re.split(r"[./]", pkg))


def is_ime_pkg(pkg: str) -> bool:
    """Public alias of _is_ime_pkg — True for soft-keyboard / IME packages."""
    return _is_ime_pkg(pkg)


# The input-method FRAMEWORK's own widget namespace (ExtractEditText — the IME's fullscreen
# editable mirror — Keyboard$Key, SoftInputWindow, ...). An exact PREFIX: package markers must
# never be matched against class paths — an app's '.imexpress'/'.latinamerica' class is NOT a
# keyboard (review finding 7: that false positive silently dropped the app's text capture).
IME_FRAMEWORK_CLASS_PREFIX = "android.inputmethodservice."


def is_ime_class(cls: str) -> bool:
    """True for the input-method framework's own widget classes (vendor-independent — holds
    even for keyboards whose package has no IME_PKG_SEGMENTS component)."""
    return bool(cls) and cls.startswith(IME_FRAMEWORK_CLASS_PREFIX)


def _should_prune(el, config: FingerprintConfig, focus_pkg: Optional[str]) -> bool:
    """Whole-subtree prune for foreign overlay windows layered on the screen."""
    pkg = el.get("package", "")
    if config.strip_ime and _is_ime_pkg(pkg) and not _is_ime_pkg(focus_pkg or ""):
        return True
    if (
        config.strip_overlay_systemui
        and pkg == SYSTEMUI_PKG
        and focus_pkg is not None
        and focus_pkg != SYSTEMUI_PKG
    ):
        return True
    return False


def _is_adapter_list(cls: str, config: FingerprintConfig) -> bool:
    return any(tok in cls for tok in config.adapter_list_classes)


# ---- adapter REGIONS beyond class names (design doc §2; the lazy-list change-set) ----
# The app-agnostic rule: a scroll-capable container whose direct children contain a >=3-run of
# one structural shape is an adapter region; its identity is the container plus its NON-repeating
# children, never the current window of the run. Detection is a PURE function of this dump (L1);
# every threshold fails toward NO collapse (L2).

# If the median stack-axis extent of the run's conforming children (relative to the container)
# reaches this band, the children are page-sized: a pager / peek carousel / full-page feed —
# NEVER collapsed (ordered pages are distinct). Values above 1.0 are vetoed too (children
# larger than the viewport are page-like a fortiori — same fragment-only failure direction).
NO_COLLAPSE_EXTENT_BAND: Tuple[float, float] = (0.70, 1.00)
_SHAPE_DEPTH = 2          # bounded-depth child pattern in the shape string
_MAX_INTERIOR_OUTLIERS = 2  # T8: a 3rd non-conforming child breaks the run (fragment direction)
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# Framework path prefixes whose classes are NAMED widgets — a container bearing one is not
# "dump-level anonymous". Everything else (android.view.View/ViewGroup, empty, obfuscated
# minified names, custom app classes) is anonymous: on toolkits that don't map semantics to
# real widget classes, the class attribute carries no signal.
_NAMED_FRAMEWORK_PREFIXES = (
    "android.widget.", "android.webkit.", "android.app.", "android.inputmethodservice.",
    "androidx.", "android.support.",
)
_ANONYMOUS_CLASSES = ("", "android.view.View", "android.view.ViewGroup")


def _is_anonymous_class(cls: str) -> bool:
    if cls in _ANONYMOUS_CLASSES:
        return True
    if cls.startswith(_NAMED_FRAMEWORK_PREFIXES):
        return False
    if cls.startswith("android.view."):
        return False  # other real android.view widgets (TextureView, ...) are named
    return True  # obfuscated ('jkv') or custom classes: no framework token — anonymous


def _parse_bounds(b: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    m = _BOUNDS_RE.match(b or "")
    return tuple(int(g) for g in m.groups()) if m else None


def _shape_str(el, depth: int = _SHAPE_DEPTH) -> str:
    """Value-suppressed structural shape of a node, for run detection (§2.1 D2.3).

    Behavior bits + presence bits + a bounded-depth child pattern. The class token is
    INCLUDED deliberately (the design doc lists it as signal-free on anonymous toolkits,
    where it is identical anyway): it can only BREAK runs, never extend them — strictly
    the fragment direction L2 permits. No values, no bounds, no mutable state bits."""
    parts = [
        el.get("class", ""),
        el.get("clickable", "false"),
        el.get("checkable", "false"),
        el.get("focusable", "false"),
        "1" if el.get("text") else "0",
        "1" if el.get("content-desc") else "0",
    ]
    s = "(" + ",".join(parts)
    if depth > 0:
        s += "[" + "".join(_shape_str(c, depth - 1) for c in el if c.tag == "node") + "]"
    return s + ")"


@dataclass(frozen=True)
class AdapterRegion:
    """A detected adapter region of a container element.

    kind="class": a D1 class-named list (RecyclerView/ListView/GridView) — whole-container
    semantics, byte-identical to the pre-region behavior. kind="run": a D2 anonymous
    repeating run over the container's direct semantic children [start, end) (end exclusive,
    interior outliers swallowed); `shape` is the conforming children's canonical shape."""

    kind: str
    start: int = 0
    end: int = 0
    shape: str = ""


def _semantic_children(el) -> list:
    return [c for c in el if c.tag == "node"]


def _max_conforming_run(shapes: List[str]) -> Optional[Tuple[int, int, str]]:
    """Longest window [i, j) starting AND ending on a conforming child, with >=3 conforming
    members and <= _MAX_INTERIOR_OUTLIERS interior non-conformers. Deterministic: longest
    window wins; ties resolve to the earliest start, then the most frequent shape."""
    best: Optional[Tuple[int, int, str]] = None
    for cand, count in sorted(Counter(shapes).items(), key=lambda kv: (-kv[1], kv[0])):
        if count < 3:
            continue
        n = len(shapes)
        for i in range(n):
            if shapes[i] != cand:
                continue
            outliers = 0
            for j in range(i, n):
                if shapes[j] == cand:
                    if (j - i + 1) - outliers >= 3:
                        if best is None or (j + 1 - i) > (best[1] - best[0]):
                            best = (i, j + 1, cand)
                else:
                    outliers += 1
                    if outliers > _MAX_INTERIOR_OUTLIERS:
                        break
    return best


def _extent_band_vetoes(container_el, conforming: list) -> bool:
    """True when the conforming children are page-sized along the stack axis — or when the
    geometry is UNREADABLE (missing/corrupt bounds): uncertain extent must not collapse (L2)."""
    cb = _parse_bounds(container_el.get("bounds"))
    boxes = [_parse_bounds(k.get("bounds")) for k in conforming]
    if cb is None or any(b is None for b in boxes):
        return True
    xs = [b[0] for b in boxes]
    ys = [b[1] for b in boxes]
    vertical = (max(ys) - min(ys)) >= (max(xs) - min(xs))
    extents = sorted((b[3] - b[1]) if vertical else (b[2] - b[0]) for b in boxes)
    median = extents[len(extents) // 2]
    container_extent = (cb[3] - cb[1]) if vertical else (cb[2] - cb[0])
    if container_extent <= 0:
        return True
    return (median / container_extent) >= NO_COLLAPSE_EXTENT_BAND[0]


def adapter_region(el, config: FingerprintConfig, inherited_scroll: bool = False) -> Optional[AdapterRegion]:
    """The ONE detector predicate every collapse/suppression call site flows through (§2).

    D1: class-named lists — whole-container region, semantics unchanged from today.
    D2: dump-level-anonymous container + `scrollable='true'` on it (or inherited through a
    strictly single-child wrapper chain — T3) + a maximal consecutive >=3-run of one
    value-suppressed shape among its direct children + the extent band does not veto.
    Pure function of (element, config, inherited_scroll) — no history, no session state (L1)."""
    cls = el.get("class", "")
    if _is_adapter_list(cls, config):
        return AdapterRegion("class")
    if not _is_anonymous_class(cls):
        return None
    kids = _semantic_children(el)
    if len(kids) < 3:
        return None
    if el.get("scrollable") != "true" and not inherited_scroll:
        return None
    run = _max_conforming_run([_shape_str(k) for k in kids])
    if run is None:
        return None
    start, end, shape = run
    conforming = [k for k in kids[start:end] if _shape_str(k) == shape]
    if _extent_band_vetoes(el, conforming):
        return None
    return AdapterRegion("run", start, end, shape)


def _inherits_scroll(el, cls: str, config: FingerprintConfig, inherited: bool) -> bool:
    """A strictly single-child wrapper (anonymous or a named scroll wrapper) passes its
    scroll capability down to that child (T3 transparency)."""
    if el.get("scrollable") != "true" and not inherited:
        return False
    if len(_semantic_children(el)) != 1:
        return False
    return _is_anonymous_class(cls) or _is_scroll_wrapper(cls, config)


def _is_pager(cls: str, config: FingerprintConfig) -> bool:
    return any(tok in cls for tok in config.pager_classes)


def _is_scroll_wrapper(cls: str, config: FingerprintConfig) -> bool:
    return any(tok in cls for tok in config.scroll_wrapper_classes)


def _join(parts: List[str]) -> str:
    return "|".join(f"{len(p)}:{p}" for p in parts)


def _desc_shape(desc: str, config: FingerprintConfig) -> str:
    if config.include_content_desc_values:
        return desc
    return "1" if desc else "0"


def _node_tuple(el, config: FingerprintConfig, depth: int, suppress_value: bool) -> str:
    parts = [
        el.get("class", ""),
        el.get("resource-id", ""),
        el.get("clickable", "false"),
        _desc_shape(el.get("content-desc", ""), config),
    ]
    if config.include_text:
        parts.append("1" if el.get("text") else "0")
    if (
        config.title_value_max_depth
        and depth < config.title_value_max_depth
        and not suppress_value
        and el.get("text")
    ):
        parts.append("t=" + el.get("text", ""))
    return "(" + _join(parts) + ")"


def _consecutive_collapse(sigs: List[str]) -> List[str]:
    return [sig for sig, _ in groupby(sigs)]  # collapse runs of identical consecutive sigs


def _depth_summary(el) -> str:
    classes = set()
    n = 0
    for d in el.iter("node"):
        if d is el:
            continue
        n += 1
        classes.add(d.get("class", ""))
    return f"{{d:{n}:{','.join(sorted(classes))}}}"


def _children_sigs(el, config, depth, suppress_value, focus_pkg, inherited_scroll=False) -> List[str]:
    cls = el.get("class", "")
    inherit_next = _inherits_scroll(el, cls, config, inherited_scroll)
    return [
        s
        for s in (
            _sig(c, config, depth + 1, suppress_value, focus_pkg, inherit_next)
            for c in el
            if c.tag == "node"
        )
        if s
    ]


def _sig(el, config: FingerprintConfig, depth: int, suppress_value: bool, focus_pkg,
         inherited_scroll: bool = False) -> str:
    if _should_prune(el, config, focus_pkg):
        return ""  # foreign overlay window (IME / SystemUI) — drop subtree
    denied = _denylisted(el.get("resource-id", ""), config)
    if depth >= config.max_depth:
        if denied:
            return ""
        summary = _depth_summary(el) if config.shallow_summary else ""
        return _node_tuple(el, config, depth, suppress_value) + summary

    cls = el.get("class", "")
    region = (
        adapter_region(el, config, inherited_scroll)
        if not denied and config.scroll_invariant_lists
        else None
    )
    if region is not None and region.kind == "class":
        # D1, byte-identical to the pre-region behavior:
        # scroll-invariant — the list is its own identity; current children dropped
        return _node_tuple(el, config, depth, suppress_value) + "[~]"

    if not denied and _is_pager(cls, config):
        # ordered pages — tabs/pages are distinct; never scroll-dropped
        child_sigs = _consecutive_collapse(
            _children_sigs(el, config, depth, suppress_value, focus_pkg, inherited_scroll))
        return _node_tuple(el, config, depth, suppress_value) + "[" + "".join(child_sigs) + "]"

    if region is not None:
        # D2 (§2.3): the region is the RUN, not the container. The run's window is replaced
        # by a value-suppressed marker (window-invariant by construction); leading/trailing
        # non-conforming children stay FULLY hashed — values included — so a header/title
        # keeps splitting otherwise-identical sibling screens.
        kids = _semantic_children(el)
        inherit_next = _inherits_scroll(el, cls, config, inherited_scroll)
        lead = _consecutive_collapse([
            s for s in (_sig(c, config, depth + 1, suppress_value, focus_pkg, inherit_next)
                        for c in kids[:region.start]) if s])
        trail = _consecutive_collapse([
            s for s in (_sig(c, config, depth + 1, suppress_value, focus_pkg, inherit_next)
                        for c in kids[region.end:]) if s])
        marker = f"~{len(region.shape)}:{region.shape}[~]"
        return (_node_tuple(el, config, depth, suppress_value)
                + "[" + "".join(lead) + marker + "".join(trail) + "]")

    in_list = suppress_value or _is_adapter_list(cls, config)
    child_sigs = _children_sigs(el, config, depth, in_list, focus_pkg, inherited_scroll)
    if config.list_collapse:
        if _is_adapter_list(cls, config):
            child_sigs = sorted(set(child_sigs))  # order/count-invariant inside an adapter list
        else:
            child_sigs = _consecutive_collapse(child_sigs)  # ordered layouts (incl. scroll wrappers)
    inner = "".join(child_sigs)
    if denied:
        return inner  # splice children up; drop the volatile node's own tuple
    return _node_tuple(el, config, depth, suppress_value) + "[" + inner + "]"


def structural_signature(
    xml: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> str:
    """Canonical structural signature of a UIAutomator hierarchy (§7).

    Sanitizes foreign overlay windows (IME, non-focused SystemUI) first, then builds
    a nested tuple-tree of (class, resource-id, clickable, content-desc-shape) dropping
    values/text/bounds, with adapter-list scroll-invariance, pager ordering, and
    volatile-chrome stripping. Returns MALFORMED on empty/truncated XML (no crash).
    """
    config = config or FingerprintConfig()
    if not xml or not xml.strip():
        return MALFORMED
    try:
        root = _xml_fromstring(xml)
    except ParseError:
        return MALFORMED
    top = [s for s in (_sig(c, config, 0, False, focus_pkg) for c in root if c.tag == "node") if s]
    return "".join(_consecutive_collapse(top))


def fingerprint(
    namespace: str, xml: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> str:
    """Screen id = short hash of (namespace + structural signature).

    The LAUNCHER is namespace-dominant (structure ignored): one activity hosts many
    jittery states (swipe-pages, widgets, page indicator, folders) that no structural
    abstraction stabilizes, and it is a force-anchor (reached via keyevent 3), not a
    structural navigation target — so all launcher states collapse to one 'home' node.
    """
    if is_launcher_namespace(namespace):
        return "L" + hashlib.sha1(namespace.encode("utf-8")).hexdigest()[:15]
    sig = structural_signature(xml, config, focus_pkg)
    payload = f"{len(namespace)}:{namespace}|{sig}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# Content-free structure tier (DroidBot `structure_str` analog): identity built from
# (class, resource-id, clickable, content-desc-presence) ONLY — no text/title values,
# whatever the screen's resolved profile. These are FingerprintConfig defaults already;
# named explicitly so the structure tier never drifts when VIEW_PROFILE changes.
STRUCTURE_CONFIG = FingerprintConfig(
    include_text=False, include_content_desc_values=False, title_value_max_depth=0
)


def structure_id(namespace: str, xml: str, focus_pkg: Optional[str] = None) -> str:
    """Coarse structure-tier id: a text-free skeleton hash (§ DroidBot two-level state).

    Distinct from fingerprint(): EXACT may fold in text for Compose screens, while this
    stays stable when only CONTENT changes (Compose labels, wizard step text). Two
    different items in an adapter list collapse here — that is intentional, and the
    Navigator treats a structure-only match as UNVERIFIABLE rather than a confident
    arrival. Launcher short-circuits to the SAME id as fingerprint() (no structure tier
    for home), so both tiers coincide on the one launcher node.
    """
    if is_launcher_namespace(namespace):
        return fingerprint(namespace, xml)
    sig = structural_signature(xml, STRUCTURE_CONFIG, focus_pkg)
    payload = f"{len(namespace)}:{namespace}|{sig}"
    return "S" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:15]


# ---- collision-refinement signal (task #17b): stable shallow chrome ----
# Structure twins (single-Activity sibling pages — Settings .SubSettings, Compose-nav, Flutter)
# share a text-free structure_id by design. The recorder refines them apart — ONLY after observing
# a collision — by a digest of the screen's STABLE CHROME: short text/content-desc VALUES that live
# OUTSIDE any adapter list (the toolbar title / header), the references' only identity-safe text
# tier (Fastbot2 static-text / DroidBot ≤50-char label rule). Grounded on the real Settings-twin
# corpus: outside-adapter-list with NO depth bound is what GENERALIZES (a depth bound was a
# Pixel-Settings-only constant — adversarial finding F2); an EMPTY set is None, never a
# value-bearing id (the cardinal-sin guard — finding F1). The structure tier itself is untouched.
_CHROME_VALUE_MAX_LEN = 50  # DroidBot's label-vs-content cutoff


def chrome_digest(
    xml: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> Optional[str]:
    """A digest of the screen's stable shallow chrome, or None when it has none.

    Collects short (≤50-char) text / content-desc VALUES for nodes OUTSIDE any adapter list,
    reusing the structural signature's own filters: foreign-overlay prune (`_should_prune` —
    IME, non-focused SystemUI; so focus_pkg is load-bearing: without it the status bar pollutes
    the digest) and the volatile-rid denylist. Adapter-list content contributes NOTHING (that
    is the content twins differ in). Set semantics (sorted unique) — duplicate-count jitter is
    not signal. Stored as a hash, never literals (same redaction class as a Compose fingerprint;
    edge selectors already carry these labels verbatim). Returns None on empty/malformed XML and
    on an empty value set — an empty digest must NEVER mint a confident refined id."""
    config = config or FingerprintConfig()
    if not xml or not xml.strip():
        return None
    try:
        root = _xml_fromstring(xml)
    except ParseError:
        return None
    values: set = set()

    def walk(el, in_list: bool, inherited_scroll: bool) -> None:
        if _should_prune(el, config, focus_pkg):
            return  # foreign overlay (IME / non-focused SystemUI) — drop subtree
        cls = el.get("class", "")
        if any(tok in cls for tok in config.volatile_widget_classes):
            return  # time/progress widget — its value churns; drop subtree (media-app self-collision)
        region = None if in_list else adapter_region(el, config, inherited_scroll)
        now_in_list = in_list or (region is not None and region.kind == "class")
        if not now_in_list and not _denylisted(el.get("resource-id", ""), config):
            for attr, tag in (("text", "t"), ("content-desc", "d")):
                v = (el.get(attr) or "").strip()
                if v and len(v) <= _CHROME_VALUE_MAX_LEN:
                    values.add(f"{tag}:{v}")
        inherit_next = _inherits_scroll(el, cls, config, inherited_scroll)
        for idx, child in enumerate(_semantic_children(el)):
            child_in = now_in_list or (
                region is not None and region.kind == "run"
                and region.start <= idx < region.end)
            walk(child, child_in, inherit_next)

    for top in root:
        if top.tag == "node":
            walk(top, False, False)
    if not values:
        return None
    return hashlib.sha1("|".join(sorted(values)).encode("utf-8")).hexdigest()[:16]


def refined_id(coarse_fp: str, digest: str) -> str:
    """A refined twin id: the shared coarse fingerprint composed with the distinguishing chrome
    digest. Twins share `coarse_fp` by construction (that is the collision), so the digest is what
    separates them. Recomputable from (coarse_fp, digest) alone — a live dump computes both from
    the hierarchy; a stored Screen carries its coarse id + digest — so record and replay converge
    on the same id. `T` follows the V/S/L id-marker convention."""
    return "T" + hashlib.sha1(f"{coarse_fp}|cd={digest}".encode("utf-8")).hexdigest()[:15]


def has_collapsing_list(
    xml: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> bool:
    """True when the screen contains a scroll-invariant adapter list (RecyclerView/ListView/
    GridView) whose content the chrome digest collapses out — ROW-COUNT-INDEPENDENT (true even
    for a sparse or empty list). This is the RECORDED HW2 signal (task #17b): a twin whose
    distinguishing content lives in a collapsing list has a thin, title-only chrome digest, so a
    same-titled unrecorded sibling could collide — the navigator withholds on-sight confidence.
    Keyed on the LIST'S PRESENCE, not the leaf-ratio, so a sparse first capture can't mislabel a
    genuine list page (the row-count transience the leaf-ratio `adapter_list_dominant` carries)."""
    config = config or STRUCTURE_CONFIG
    if not xml or not xml.strip():
        return False
    try:
        root = _xml_fromstring(xml)
    except ParseError:
        return False
    stack = [(c, False) for c in root if c.tag == "node"]
    while stack:
        el, inherited = stack.pop()
        if _should_prune(el, config, focus_pkg):
            continue  # foreign overlay — not the app's content
        if adapter_region(el, config, inherited) is not None:
            return True
        inherit_next = _inherits_scroll(el, el.get("class", ""), config, inherited)
        stack.extend((c, inherit_next) for c in el if c.tag == "node")
    return False


def adapter_list_dominant(
    xml: str,
    config: FingerprintConfig = None,
    threshold: float = 0.6,
    focus_pkg: Optional[str] = None,
) -> bool:
    """True when most leaf nodes live inside an adapter list (RecyclerView/ListView/
    GridView). Such a screen's structure is content-free by construction (the list
    collapses to '[~]'), so two DIFFERENT list screens (inbox vs archive) can share a
    structure_id — the Navigator must treat a structure-only match here as UNVERIFIABLE
    rather than a confident arrival (§ verify tiers).

    Overlay leaves (IME soft keyboard, non-focused SystemUI) are excluded from BOTH the
    numerator and denominator, mirroring what structure_id() actually hashes — otherwise a
    keyboard's ~30 key leaves dilute the ratio below threshold and silently defeat the
    UNVERIFIABLE guard on a search/list screen (review #3)."""
    config = config or STRUCTURE_CONFIG
    if not xml or not xml.strip():
        return False
    try:
        root = _xml_fromstring(xml)
    except ParseError:
        return False
    total = in_region = 0

    def walk(el, inside: bool, inherited: bool) -> None:
        nonlocal total, in_region
        if _should_prune(el, config, focus_pkg):
            return  # overlay leaves (IME keys, SystemUI) out of BOTH counts
        kids = _semantic_children(el)
        if not kids:
            total += 1
            in_region += 1 if inside else 0
            return
        region = None if inside else adapter_region(el, config, inherited)
        inherit_next = _inherits_scroll(el, el.get("class", ""), config, inherited)
        for idx, child in enumerate(kids):
            child_inside = inside or (
                region is not None
                and (region.kind == "class" or region.start <= idx < region.end))
            walk(child, child_inside, inherit_next)

    for top in root:
        if top.tag == "node":
            walk(top, False, False)
    if not total:
        return False
    return in_region / total >= threshold


def region_geometry(
    xml: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> list:
    """Detected adapter regions with the geometry/content the RECORDER's gesture
    classification needs (§2.7 reveal sense / §2.8 tripwire) — NOT part of identity.

    Per region, document order: {"bounds": (l,t,r,b) of the container, "axis": 'y'|'x'
    (stack axis of the region children), "digests": ordered per-child sha1 of the child
    subtree's VALUE content (text / content-desc / resource-id, VOLATILE-widget subtrees
    stripped — an in-row progress bar must not read as content change), "child_boxes":
    ordered per-child bounds (None when unparseable)} — run-span children for an anonymous
    run, all children for a class-named list. Region containers are not descended into
    (nested regions inside a run are not classification targets)."""
    config = config or FingerprintConfig()
    if not xml or not xml.strip():
        return []
    try:
        root = _xml_fromstring(xml)
    except ParseError:
        return []
    out: list = []

    def child_digest(c) -> str:
        vals: list = []

        def collect(d) -> None:
            if any(tok in d.get("class", "") for tok in config.volatile_widget_classes):
                return  # time/progress widget: its value churns every dump — never content
            for attr in ("text", "content-desc", "resource-id"):
                v = d.get(attr)
                if v:
                    vals.append(f"{attr}:{v}")
            for k in d:
                if k.tag == "node":
                    collect(k)

        collect(c)
        return hashlib.sha1("|".join(vals).encode("utf-8")).hexdigest()[:12]

    def walk(el, inherited: bool) -> None:
        if _should_prune(el, config, focus_pkg):
            return
        region = adapter_region(el, config, inherited)
        if region is not None:
            kids = _semantic_children(el)
            span = kids if region.kind == "class" else kids[region.start:region.end]
            bounds = _parse_bounds(el.get("bounds"))
            boxes = [b for b in (_parse_bounds(k.get("bounds")) for k in span) if b]
            if len(boxes) >= 2:
                ys = [b[1] for b in boxes]
                xs = [b[0] for b in boxes]
                axis = "y" if (max(ys) - min(ys)) >= (max(xs) - min(xs)) else "x"
            else:
                axis = "y"
            if bounds is not None:
                out.append({"bounds": bounds, "axis": axis,
                            "digests": [child_digest(k) for k in span],
                            "child_boxes": [_parse_bounds(k.get("bounds")) for k in span]})
            return
        inherit_next = _inherits_scroll(el, el.get("class", ""), config, inherited)
        for c in _semantic_children(el):
            walk(c, inherit_next)

    for top in root:
        if top.tag == "node":
            walk(top, False)
    return out


def outside_region_value_bearing(
    xml: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> bool:
    """L3 — value-bearing is a fact about the HASH, not the profile.

    True iff at least one text/content-desc VALUE outside every detected adapter region
    survives into the fingerprint hash under `config` (i.e. a title value within
    `title_value_max_depth`, or a desc value when `include_content_desc_values`). A screen
    where every surviving value lives inside a region has an id an unrecorded sibling can
    reproduce — the navigator must withhold on-sight confidence for it (recorded as
    `Screen.value_bearing` from the same settled dump; the guard keys on the recorded bit)."""
    config = config or FingerprintConfig()
    if not (config.title_value_max_depth or config.include_content_desc_values):
        return False  # this profile never hashes values: nothing can be value-bearing
    if not xml or not xml.strip():
        return False
    try:
        root = _xml_fromstring(xml)
    except ParseError:
        return False
    found = False

    def walk(el, depth: int, in_region: bool, inherited: bool) -> None:
        nonlocal found
        if found or _should_prune(el, config, focus_pkg):
            return
        denied = _denylisted(el.get("resource-id", ""), config)
        if not in_region and not denied:
            if (config.title_value_max_depth and depth < config.title_value_max_depth
                    and el.get("text")):
                found = True
                return
            if config.include_content_desc_values and el.get("content-desc"):
                found = True
                return
        if depth >= config.max_depth:
            return  # past the cap only a summary is hashed — no values survive
        region = None if in_region else adapter_region(el, config, inherited)
        now_in = in_region or (region is not None and region.kind == "class")
        inherit_next = _inherits_scroll(el, el.get("class", ""), config, inherited)
        for idx, child in enumerate(_semantic_children(el)):
            child_in = now_in or (
                region is not None and region.kind == "run"
                and region.start <= idx < region.end)
            walk(child, depth + 1, child_in, inherit_next)

    for top in root:
        if top.tag == "node":
            walk(top, 0, False, False)
    return found


def stabilize(
    xml1: str, xml2: str, config: FingerprintConfig = None, focus_pkg: Optional[str] = None
) -> Tuple[str, bool]:
    """Double-dump equality helper (§7). The recorder uses settle() for the real
    N-consecutive settle; this reports whether two dumps share a signature."""
    s1 = structural_signature(xml1, config, focus_pkg)
    s2 = structural_signature(xml2, config, focus_pkg)
    return s1, s1 == s2
