"""§3 — the scroll-to-reveal rung: "selector absent after poll" becomes "absent after a
BOUNDED reveal", with typed observation-only refusals.

ONE implementation, two callers (the replay engine's `_run_command` and the navigator's
edge walk), both entering at their presence-exhausted point and both behind their own
verified-source gate (L6 — the rung itself never decides screen trust).

The laws this code is bound by (design doc 2026-06-09-compose-lazy-design-input.md):
  L4 — typed stops name only what the dump SHOWED ("no observable region change after N
       content-advance steps; selector not found"), never an inference ("the list ended").
  L5 — check and act are ONE resolution: the tap is bounds-anchored to the verified
       in-region match from the SAME settled dump; never a fresh global first-match lookup.
  §3.3 — container binding is recorded-evidence ONLY: the recorded reveal gesture's start
       point, else the action's recorded bind bounds; no largest-scrollable fallback, no
       screen-area swipe (an unbound swipe can drag a SeekBar) — refuse `reveal_no_container`.
  §3.4 — the sense is ALWAYS content-advance, so the pull-to-refresh sense is unreachable
       by construction; the axis comes from the live region's child stacking.
  §3.5 — recorded reveal gestures replay FIRST (faithfulness), each counted against the
       same step/wall budget with a re-check after every gesture; uniqueness is REQUIRED
       (items legally repeat — a lookalike row produces `reveal_ambiguous`, never a
       first-match tap); every wait is a polled condition on the injectable clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _xml_fromstring

from wendle.fingerprint.signature import _parse_bounds, region_geometry
from wendle.models import Selector

# typed outcomes (value-free constants; the engine maps them onto StopReason, the
# navigator carries them in NavOutcome.reveal)
REVEALED = "revealed"
NOT_ELIGIBLE = "not_eligible"          # the rung does not apply — callers keep today's stop
NO_CONTAINER = "reveal_no_container"   # no recorded evidence binds a container (or it vanished)
AMBIGUOUS = "reveal_ambiguous"         # >=2 in-container matches — refuse, never first-match
NO_MOVEMENT = "reveal_no_movement"     # a step produced no observable region change
BUDGET = "reveal_budget"               # step/wall cap — the only terminator on endless feeds
OFF_TARGET = "reveal_off_target"       # the walk recognized a DIFFERENT known screen — stop typed
RETREAT_REFUSED = "reveal_retreat"     # the recorded gesture's sense is not content-advance (§3.4)

MAX_STEPS = 30        # UiScrollable maxSearchSwipes
WALL_BUDGET = 20.0    # seconds — Maestro's default element budget
_INSET = 0.10         # swipe inset per edge (UiScrollable deadzone; avoids system edge gestures)
# When the container shares an edge with the SCREEN, a container-relative inset can still
# land inside the system gesture zone (back / home / shade) and the OS swallows the swipe —
# an ineffective gesture that then honestly (but uselessly) reads as no_movement. The inset
# at a shared edge grows to this screen-fraction clearance. S23-bisected: interception
# reached ~9.3% of screen height (OEM zones exceed AOSP's); 12% adds margin. The failure
# direction is safe — a larger inset only shortens per-step travel (more steps, budget-
# bounded), it can never cause a wrong action.
_EDGE_CLEAR = 0.12
_SWIPE_SECONDS = 0.6  # Maestro speed-40 equivalent (recorded-gesture replay)
# A GENERATED container advance must be FLING-FREE (S23 overshoot root cause): a fast swipe
# flings the list far past the target's viewport, so the rung scrolls right past the row.
# Duration is scaled to distance at this bounded speed (UiScrollable-style controlled scroll),
# floored so a tiny step still registers.
_ADVANCE_PX_PER_SEC = 800.0
_ADVANCE_MIN_SECONDS = 0.4


def _advance_seconds(distance: int) -> float:
    return max(_ADVANCE_MIN_SECONDS, abs(distance) / _ADVANCE_PX_PER_SEC)
_QUANT = 0.02         # child-bounds jitter quantization, fraction of container extent

# selector kind -> dump attributes it matches (exact-attribute semantics for the legacy
# kinds; `label` is the §4 UNION — text ∪ content-desc ∪ hint. coords/keyevent are never
# reveal-eligible: an in-region coordinate is meaningless once content moves)
_SELECTOR_ATTR = {
    "resource_id": ("resource-id",),
    "text": ("text",),
    "content_desc": ("content-desc",),
    "hint": ("hint",),
    "label": ("text", "content-desc"),  # hints are FIELD handles, never tap targets (S23)
}


@dataclass
class RevealReport:
    """A typed, value-free account of one reveal attempt (NavOutcome.reveal rides this).

    `acted`: REVEALED only. True = a TAP-class action was performed inline, bounds-anchored
    to the matched node (L5 — atomic, no re-resolution). False = the element was brought
    on-screen but NOT acted on (a set_text / set_checked / non-tap action); the CALLER runs
    the recorded action against the now-present selector. A bare coordinate tap can faithfully
    reproduce only click / long_click — anything else acted here would be a confident-wrong
    action (a tapped-not-typed password field), so the rung refuses to consummate it."""

    reason: str
    steps: int = 0
    selector_kind: str = ""
    bound: str = ""  # which budget fired ('steps' | 'wall'), reveal_budget only
    acted: bool = False

    def __repr__(self) -> str:  # value-free by construction
        extra = f" bound={self.bound}" if self.bound else ""
        return f"<reveal {self.reason} kind={self.selector_kind} steps={self.steps}{extra}>"


_TAP_ACTIONS = ("click", "long_click")


def is_content_advance(axis: str, px: int, py: int, end) -> bool:
    """True when a gesture from (px,py) to `end` is a CONTENT-ADVANCE swipe along `axis`:
    motion toward the axis start (up for 'y', left for 'x') AND axis-dominant (the on-axis
    component exceeds the off-axis one — a near-horizontal pan on a vertical list is NOT a
    vertical advance). Shared by the recorder's reveal classification and continuity check so
    the two cannot drift (the §2.7 rule that pans / retreats never become replayable reveals)."""
    if end is None:
        return False
    dx, dy = end[0] - px, end[1] - py
    if axis == "y":
        return dy < 0 and abs(dy) >= abs(dx)
    return dx < 0 and abs(dx) >= abs(dy)


def _recorded_reveals(screen) -> list:
    return [a for a in (getattr(screen, "intra_actions", None) or [])
            if a.intent == "reveal" and a.action_type == "swipe"]


def eligible(action, source_screen) -> bool:
    """§3.1 — selector-scoped recorded evidence only: the action was bound inside a detected
    region, OR its source screen carries recorded region-bound reveal gestures. Screen-level
    scroll history grants nothing; legacy graphs (fields absent) never trigger."""
    if action.selector.kind not in _SELECTOR_ATTR:
        return False
    if getattr(action, "in_region", False):
        return True
    return source_screen is not None and bool(_recorded_reveals(source_screen))


def _region_containing(regions: list, x: int, y: int) -> Optional[dict]:
    for r in regions:
        left, top, right, bottom = r["bounds"]
        if left <= x <= right and top <= y <= bottom:
            return r
    return None


def _overlap_area(a, b) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if (w > 0 and h > 0) else 0


def _rebind(regions: list, prev_bounds) -> Optional[dict]:
    best, best_area = None, 0
    for r in regions:
        area = _overlap_area(r["bounds"], prev_bounds)
        if area > best_area:
            best, best_area = r, area
    return best


def _bind_container(regions: list, action, source_screen) -> Optional[dict]:
    """§3.3 recorded-evidence ladder: (a) the recorded reveal gesture's start point;
    (b) the region enclosing the action's recorded bind bounds; (c) None — typed refusal."""
    for g in reversed(_recorded_reveals(source_screen) if source_screen else []):
        if g.selector.kind == "coords" and g.selector.value:
            x, y = g.selector.value
            region = _region_containing(regions, int(x), int(y))
            if region is not None:
                return region
    b = getattr(action, "bounds", None)
    if b:
        return _region_containing(regions, (b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    return None


def _region_state(region: dict):
    """The value-bearing comparison state of a bound region (§3.6): ordered child value
    digests (volatile-stripped) + child bounds quantized to 2% of the container extent —
    NOT the value-suppressed structural signature (every window of a homogeneous feed is
    identical there by design) and NOT raw XML (pixel jitter)."""
    left, top, right, bottom = region["bounds"]
    extent = max(right - left, bottom - top, 1)
    q = max(1, int(extent * _QUANT))
    boxes = tuple(
        None if b is None else (b[0] // q, b[1] // q, b[2] // q, b[3] // q)
        for b in region.get("child_boxes", []))
    return tuple(region["digests"]), boxes


def _smallest_containing(root, left, top, right, bottom, parents):
    """The smallest-area node whose bounds CONTAIN (left,top,right,bottom) (2px tolerance),
    and the set of ids in its subtree. Shared container-identification (region_geometry can
    report a clipped content box smaller than the scrollable node's literal bounds)."""
    container, c_area = None, None
    for el in root.iter("node"):
        b = _parse_bounds(el.get("bounds"))
        if b is None or not (b[0] <= left + 2 and b[1] <= top + 2
                             and b[2] >= right - 2 and b[3] >= bottom - 2):
            continue
        area = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
        if c_area is None or area < c_area:
            container, c_area = el, area
    sub = set()
    if container is not None:
        stack = list(container)
        while stack:
            n = stack.pop()
            sub.add(id(n))
            stack.extend(n)
    return container, sub


def node_in_region_subtree(xml: str, node_bounds, regions=None) -> bool:
    """True iff the node at `node_bounds` is a DOM DESCENDANT of a detected adapter region's
    container — NOT merely geometrically inside its bounds. This is what `in_region` must mean:
    a list ROW is a descendant of the scrollable region (in_region -> the reveal rung owns its
    replay), but a FLOATING tab bar / FAB / snackbar whose pixels overlap the region is a
    SIBLING DOM branch (global chrome -> a normal tap, never reveal-scrolled). The geometric-
    only test mis-stamped global-nav affordances and broke single-Activity tab routing."""
    from wendle.fingerprint.signature import region_geometry
    if regions is None:
        regions = [r["bounds"] for r in region_geometry(xml)]
    if not regions:
        return False
    nl, nt, nr, nb = node_bounds
    ncx, ncy = (nl + nr) // 2, (nt + nb) // 2
    geo = [rb for rb in regions if rb[0] <= ncx <= rb[2] and rb[1] <= ncy <= rb[3]]
    if not geo:
        return False
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return True  # cannot parse -> fall back to the geometric answer (eligibility-preserving)
    parents = {c: p for p in root.iter() for c in p}
    # the tapped node = the exact-bounds match closest to node_bounds (deepest such)
    target = None
    for el in root.iter("node"):
        if _parse_bounds(el.get("bounds")) == tuple(node_bounds):
            target = el  # last (deepest in doc order) exact match
    if target is None:
        return True  # node not locatable in this dump -> keep the geometric answer
    for rb in geo:
        _c, sub = _smallest_containing(root, rb[0], rb[1], rb[2], rb[3], parents)
        if id(target) in sub:
            return True
    return False


def _matches_in_container(xml: str, kind: str, value, container_bounds) -> list:
    """Visible nodes matching the selector that are DOM DESCENDANTS of the bound container,
    with DEEPEST-MATCH dedup: when one match is an ancestor of another (a row container
    whose label merged from its leaf — the §4 case `pick_unique_deepest` handles on-screen),
    the ancestor is dropped so the rung's uniqueness test agrees with the driver's. Without
    this, an ancestor+leaf pair falsely reads as AMBIGUOUS and refuses a resolvable element.

    CONTAINMENT IS DOM-SUBTREE MEMBERSHIP, NOT GEOMETRY (S23 floating-pill finding): an
    overlay (floating search pill, FAB, snackbar) sits geometrically inside the list's
    bounding box while living on a SIBLING DOM branch — and Samsung's pill ROTATES its text
    through real row labels, so bounds-containment matched and tapped the pill instead of
    scrolling to the real row. The container element(s) are located by their exact recorded
    bounds; only their descendants can match. Out-of-container matches are ignored — never
    acted on (Appium #11247)."""
    attrs = _SELECTOR_ATTR[kind]
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return []
    left, top, right, bottom = container_bounds
    parents = {c: p for p in root.iter() for c in p}
    # locate the container element: the SMALLEST-area node whose bounds CONTAIN the region
    # (region_geometry may report a clipped CONTENT box smaller than the scrollable node's
    # literal full-screen bounds — an exact-bounds key then finds nothing and the rung scrolls
    # past a present row, the S23 regression). Containment keeps the sibling overlay (a pill
    # that does NOT contain the list region) out of the subtree. Tolerance absorbs 1px clip.
    def _contains(b) -> bool:
        return (b[0] <= left + 2 and b[1] <= top + 2
                and b[2] >= right - 2 and b[3] >= bottom - 2)

    container = None
    c_area = None
    for el in root.iter("node"):
        b = _parse_bounds(el.get("bounds"))
        if b is None or not _contains(b):
            continue
        area = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
        if c_area is None or area < c_area:
            container, c_area = el, area
    in_subtree = set()
    if container is not None:
        stack = list(container)
        while stack:
            n = stack.pop()
            in_subtree.add(id(n))
            stack.extend(n)
    # OCCLUSION (S23 z-order finding): a row can be a genuine DOM child of the region yet
    # sit UNDER a floating overlay (bottom search pill, FAB, snackbar) — the dump carries no
    # z-order, but Android draws later document-order branches ON TOP, so the physical tap
    # at the row's center hits the overlay. Hit-test the tap point z-aware (deepest node per
    # branch; LAST in document order wins) and skip a match someone else would swallow — the
    # rung keeps scrolling until the row surfaces clear. Eligibility-denying direction only.
    doc_order = {id(el): i for i, el in enumerate(root.iter("node"))}

    def _descendant_ids(el) -> set:
        out, stack = set(), list(el)
        while stack:
            n = stack.pop()
            out.add(id(n))
            stack.extend(n)
        return out

    def _occluders_of(match_el):
        """Foreign (non-own, non-ancestor) later-drawn nodes overlapping the match — the
        overlays whose draw order covers it."""
        own = _descendant_ids(match_el) | {id(match_el)}
        anc, cur = set(), parents.get(match_el)
        while cur is not None:
            anc.add(id(cur))
            cur = parents.get(cur)
        mb = _parse_bounds(match_el.get("bounds"))
        out = []
        for el in root.iter("node"):
            if id(el) in own or id(el) in anc or el is match_el:
                continue
            # only a CLICKABLE later-drawn node actually CONSUMES the touch and thus occludes;
            # a non-clickable decorative overlay (OEM round-corner / scrim / ripple layer that
            # spans the screen) is TOUCH-TRANSPARENT — taps fall through it (S23 round_corner
            # finding: a full-screen decoration was flagging every row as occluded).
            if el.get("clickable") != "true" and el.get("long-clickable") != "true":
                continue
            b2 = _parse_bounds(el.get("bounds"))
            if b2 is None:
                continue
            if b2[2] <= mb[0] or b2[0] >= mb[2] or b2[3] <= mb[1] or b2[1] >= mb[3]:
                continue  # no overlap
            if doc_order[id(el)] > doc_order[id(match_el)]:  # drawn LATER (on top)
                out.append(b2)
        return mb, out

    def _clear_box(match_el):
        """The match's largest tappable sub-box CLEAR of later-drawn overlays, or None when
        fully covered. Trims the box along the vertical axis (overlays here are bottom pills /
        top bars); a point-tap there is unoccluded."""
        mb, occ = _occluders_of(match_el)
        top_, bot_ = mb[1], mb[3]
        for ob in occ:
            # only an overlay spanning the match's full WIDTH (a bar/pill) trims vertically;
            # a partial-width overlay is handled by the center hit-test fallback below
            if ob[0] <= mb[0] and ob[2] >= mb[2]:
                if ob[1] <= top_:           # covers from the top
                    top_ = max(top_, ob[3])
                if ob[3] >= bot_:           # covers from the bottom
                    bot_ = min(bot_, ob[1])
        if bot_ - top_ < 8:                  # nothing meaningfully clear left
            return None
        cy = (top_ + bot_) // 2
        # the clear strip's center must itself be unoccluded by any remaining overlay
        for ob in occ:
            if ob[0] <= (mb[0] + mb[2]) // 2 <= ob[2] and ob[1] <= cy <= ob[3]:
                return None
        return (mb[0], top_, mb[2], bot_)

    matched = []  # (element, clear-bounds)
    for el in root.iter("node"):
        if id(el) not in in_subtree:
            continue
        if not any(el.get(a) == value for a in attrs):
            continue
        if el.get("visible-to-user") not in (None, "true"):  # absent attr (old dumps) = visible
            continue
        b = _parse_bounds(el.get("bounds"))
        if b is None:
            continue
        cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        if left <= cx <= right and top <= cy <= bottom:
            clear = _clear_box(el)
            if clear is None:
                continue  # fully covered — never tap through an overlay; scroll it clear
            matched.append((el, clear))  # act on the CLEAR sub-box (its center is visible)

    def _is_ancestor_of_another(el) -> bool:
        for other, _ in matched:
            if other is el:
                continue
            cur = parents.get(other)
            while cur is not None:
                if cur is el:
                    return True
                cur = parents.get(cur)
        return False

    return [b for el, b in matched if not _is_ancestor_of_another(el)]


def _advance_span(lo: int, hi: int, screen_dim: Optional[int]) -> Tuple[int, int]:
    """The (start, end) of a content-advance swipe along one axis of a container spanning
    [lo, hi]: start near `hi`, end near `lo` (advance = move content toward the axis start),
    each inset off the container edge and — when the container is flush with a screen edge —
    cleared off the SYSTEM GESTURE ZONES.

    The clamp MUST NOT invert the sense (start>end). If clearing the zones would collapse or
    flip the span (a short region flush with an edge), fall back to the full screen-safe band
    in the advance direction — still a content-advance swipe, never a retreat / pull-to-refresh.
    Returns (lo, lo) when no valid advance exists (degenerate); the caller skips a zero swipe."""
    inset = max(1, int((hi - lo) * _INSET))
    start, end = hi - inset, lo + inset
    if screen_dim is not None:
        clear = max(inset, int(screen_dim * _EDGE_CLEAR))
        if hi >= screen_dim - inset:
            start = min(start, screen_dim - clear)
        if lo <= inset:
            end = max(end, clear)
        if start <= end:  # the clamp would invert or collapse the swipe -> safe band, advance sense
            start, end = screen_dim - clear, clear
    # OVERLAP CAP (S23 overshoot finding): a full-extent step (plus any fling) can JUMP OVER
    # the target's only clear viewport positions, and advance-only scrolling cannot recover
    # (§3.4 forbids retreat). Cap travel at HALF the container extent so consecutive stops
    # OVERLAP — any element visible between two stops appears in at least one (UiScrollable's
    # half-viewport discipline). Slower coverage is budget-bounded; skipping is forever.
    half = max(1, (hi - lo) // 2)
    if start - end > half:
        end = start - half
    return (start, end) if start > end else (lo, lo)


def _clear_advance_band(xml: str, container_bounds, axis: str = "y"):
    """The sub-band of `container_bounds` along `axis` that is CLEAR of later-drawn FOREIGN
    overlays (a floating bottom pill / FAB / snackbar that physically covers the list's edge).
    A content-advance swipe whose start lands on such an overlay is SWALLOWED — the list never
    scrolls and the rung wrongly reads no_movement. Returns (lo, hi) shrunk away from an
    overlay touching the advance-START edge (the axis-`hi` end for a vertical advance). Foreign
    = a node NOT in the container subtree, drawn LATER (document order), overlapping the band.
    Eligibility-denying only: never grows the band, never inverts it."""
    left, top, right, bottom = container_bounds
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return (top, bottom) if axis == "y" else (left, right)
    in_subtree = set()
    for el in root.iter("node"):
        if _parse_bounds(el.get("bounds")) == (left, top, right, bottom):
            stack = list(el)
            while stack:
                n = stack.pop()
                in_subtree.add(id(n))
                stack.extend(n)
    lo, hi = (top, bottom) if axis == "y" else (left, right)
    cross_lo, cross_hi = (left, right) if axis == "y" else (top, bottom)
    for el in root.iter("node"):
        if id(el) in in_subtree:
            continue
        b = _parse_bounds(el.get("bounds"))
        if b is None:
            continue
        # overlaps the container's cross-axis span AND its on-axis band?
        if axis == "y":
            if b[2] <= left or b[0] >= right:
                continue
            o_lo, o_hi = b[1], b[3]
        else:
            if b[3] <= top or b[1] >= bottom:
                continue
            o_lo, o_hi = b[0], b[2]
        if o_hi <= lo or o_lo >= hi:
            continue  # outside the current band
        # an overlay covering the advance-START edge (the `hi` end) pulls `hi` down to its top;
        # one covering the `lo` edge pulls `lo` up (advance ends there). Keep the larger side.
        if o_hi >= hi and o_lo > lo:
            hi = min(hi, o_lo)
        elif o_lo <= lo and o_hi < hi:
            lo = max(lo, o_hi)
    return (lo, hi) if hi > lo else ((top, bottom) if axis == "y" else (left, right))


def _advance_swipe(driver, region: dict, xml: Optional[str] = None) -> None:
    """One container-derived content-advance step (§3.5.1): bounds-based, inset per edge,
    never a screen-center swipe, never inverted into a retreat (see _advance_span). When `xml`
    is given, the swipe band is first shrunk CLEAR of later-drawn foreign overlays (a floating
    bottom pill that would otherwise swallow the gesture — S23 no_movement root cause)."""
    left, top, right, bottom = region["bounds"]
    try:
        screen_w, screen_h = driver.display_size()
    except Exception:  # noqa: BLE001 — no display info: fall back to container-only insets
        screen_w = screen_h = None
    if region["axis"] == "y":
        cx = (left + right) // 2
        lo, hi = (_clear_advance_band(xml, region["bounds"], "y") if xml else (top, bottom))
        start_y, end_y = _advance_span(lo, hi, screen_h)
        if start_y != end_y:
            driver.swipe((cx, start_y), (cx, end_y), _advance_seconds(start_y - end_y))
    else:
        cy = (top + bottom) // 2
        lo, hi = (_clear_advance_band(xml, region["bounds"], "x") if xml else (left, right))
        start_x, end_x = _advance_span(lo, hi, screen_w)
        if start_x != end_x:
            driver.swipe((start_x, cy), (end_x, cy), _advance_seconds(start_x - end_x))


def attempt_reveal(driver, action, source_screen, observe: Callable, *,
                   clock: Callable[[], float], sleep=None,
                   max_steps: int = MAX_STEPS, wall_budget: float = WALL_BUDGET) -> RevealReport:
    """Run the bounded reveal loop for an absent selector. The CALLER guarantees the source
    screen is verified (L6) and that a presence wait already exhausted. On `revealed` the
    bounds-anchored act has ALREADY been issued from the matching settled dump (L5) — the
    caller proceeds to its normal arrival verification; on any other reason nothing further
    was tapped and the caller stops typed."""
    kind = action.selector.kind
    if not eligible(action, source_screen):
        return RevealReport(NOT_ELIGIBLE, 0, kind)
    deadline = clock() + wall_budget
    xml, _ns, focus, _settled = observe()
    regions = region_geometry(xml, focus_pkg=focus)
    region = _bind_container(regions, action, source_screen)
    if region is None:
        return RevealReport(NO_CONTAINER, 0, kind)
    prev_state = _region_state(region)
    recorded = list(_recorded_reveals(source_screen) if source_screen else [])
    steps = 0
    while True:
        found = _matches_in_container(xml, kind, action.selector.value, region["bounds"])
        if len(found) == 1:
            box = found[0]
            if action.action_type in _TAP_ACTIONS:
                # L5: act FROM this settled dump — a coordinate derived from a fresh verified
                # observation, not a recorded blind pixel and not a global re-resolution.
                # Honor the action_type (a long_click must not degrade to a click).
                driver.resolve_and_tap(
                    Selector("coords", ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)),
                    action.action_type)
                return RevealReport(REVEALED, steps, kind, acted=True)
            # a non-tap action (set_text / set_checked): the element is now on screen, but a
            # bare coordinate tap cannot faithfully perform it — hand back to the caller, which
            # runs the recorded action against the now-present selector (never a silent skip).
            return RevealReport(REVEALED, steps, kind, acted=False)
        if len(found) > 1:
            return RevealReport(AMBIGUOUS, steps, kind)
        if steps >= max_steps:
            return RevealReport(BUDGET, steps, kind, bound="steps")
        if clock() >= deadline:
            return RevealReport(BUDGET, steps, kind, bound="wall")
        # §3.5.0: recorded reveal gestures first (budgeted, re-checked per gesture),
        # then generic container-derived advance steps.
        if recorded:
            g = recorded.pop(0)
            if g.selector.kind == "coords" and g.selector.value and g.end:
                driver.swipe(tuple(g.selector.value), tuple(g.end), _SWIPE_SECONDS)
            else:
                _advance_swipe(driver, region, xml)
        else:
            _advance_swipe(driver, region, xml)
        steps += 1
        xml, _ns, focus, _settled = observe()
        regions = region_geometry(xml, focus_pkg=focus)
        rebound = _rebind(regions, region["bounds"])
        if rebound is None:
            # the bound container is no longer observable — refuse; "no movement" would
            # claim an observation we did not make (L4)
            return RevealReport(NO_CONTAINER, steps, kind)
        region = rebound
        state = _region_state(region)
        if state == prev_state:
            return RevealReport(NO_MOVEMENT, steps, kind)
        prev_state = state


def walk_to_node(driver, scroll_action, source_screen, observe, *, resolves, recognized_other,
                 clock, sleep=None, max_steps=MAX_STEPS, wall_budget=WALL_BUDGET) -> RevealReport:
    """Walk a chrome-fork SCROLL edge to its target twin (Cap 1). Distinct from attempt_reveal:
    the stop condition is NODE IDENTITY, not a selector match, and the gesture is the recorded
    fork scroll (a coords-selector reveal swipe) — so the eligible()/selector-match machinery
    does not apply. Shares the bind/rebind/advance/no-movement skeleton.

    `resolves(xml, ns, focus) -> bool`  : does the live observation reproduce the TARGET id (via
                                          the caller's strict, ambiguity-guarded arrival gate)?
    `recognized_other(xml, ns, focus)`  : does it strictly reproduce a DIFFERENT known graph
                                          node? (stop typed — never keep mutating a known screen)

    The CALLER guarantees the L6 source gate (the live screen EXACT-reproduces the edge source)
    BEFORE calling. This function never makes an arrival claim and never grants corroboration —
    it only moves the device toward the twin. Direction is the recorded gesture's sense, RE-
    VALIDATED content-advance against each live bound region (a legacy/hand-edited retreat edge
    is refused, never issued — §3.4). Bounded on the injectable clock; every non-REVEALED reason
    is an honest typed stop."""
    deadline = clock() + wall_budget
    start = tuple(scroll_action.selector.value) if scroll_action.selector.kind == "coords" else None
    end = tuple(scroll_action.end) if scroll_action.end else None
    xml, ns, focus, _settled = observe()
    # 0-step success: routing may have started us already on the target twin.
    if resolves(xml, ns, focus):
        return RevealReport(REVEALED, 0, "scroll", acted=False)
    # 0-step FOREIGN check too: a timed UI event (interstitial, auto-advance) can move the
    # device between the caller's L6 observation and ours — never issue even one mutating
    # swipe against a screen already strictly recognized as the wrong one.
    if recognized_other(xml, ns, focus):
        return RevealReport(OFF_TARGET, 0, "scroll")
    regions = region_geometry(xml, focus_pkg=focus)
    region = _bind_container(regions, scroll_action, source_screen)
    if region is None:
        return RevealReport(NO_CONTAINER, 0, "scroll")
    prev_state = _region_state(region)
    steps = 0
    while True:
        if steps >= max_steps:
            return RevealReport(BUDGET, steps, "scroll", bound="steps")
        if clock() >= deadline:
            return RevealReport(BUDGET, steps, "scroll", bound="wall")
        # re-validate the recorded gesture's sense against the LIVE bound region's axis — a
        # retreat/pan (legacy or hand-edited edge) is refused, never issued (§3.4).
        if start is None or not is_content_advance(region["axis"], start[0], start[1], end):
            return RevealReport(RETREAT_REFUSED, steps, "scroll")
        _advance_swipe(driver, region, xml)
        steps += 1
        xml, ns, focus, _settled = observe()
        if resolves(xml, ns, focus):
            return RevealReport(REVEALED, steps, "scroll", acted=False)
        if recognized_other(xml, ns, focus):
            # we left the fork onto a DIFFERENT recognized screen — stop typed rather than keep
            # issuing state-mutating swipes against a known-wrong state.
            return RevealReport(OFF_TARGET, steps, "scroll")
        regions = region_geometry(xml, focus_pkg=focus)
        rebound = _rebind(regions, region["bounds"])
        if rebound is None:
            return RevealReport(NO_CONTAINER, steps, "scroll")
        region = rebound
        state = _region_state(region)
        if state == prev_state:
            return RevealReport(NO_MOVEMENT, steps, "scroll")
        prev_state = state
