"""Detection of GLOBAL NAVIGATION CHROME — a tab bar / bottom nav / nav drawer: a group of
clickable, content-desc-bearing sibling affordances present on ~every screen of a section-based
app, each opening a top-level section. CONSERVATIVE by contract: uncertain -> empty (a normal
edge), so non-tabbed apps are byte-identical and never regress. Pure function of the dump; the
crawl ingester supplies the same xml, so the convergence lock holds.

Why a positive discriminator (not just "a row of clickables"): a content RUN of identical rows
(a list) and a SEGMENTED control (Day/Week/Month) are structurally a row/column of clickables
too. A nav container is distinguished by EITHER carrying a per-item `selected` signal (a nav bar
exposes which section is active) OR a nav-toolkit class on the container/items."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _xml_fromstring

# Android nav-toolkit classes — a positive class signal that a container is global nav.
_NAV_CLASSES = (
    "BottomNavigationView", "BottomNavigationItemView", "NavigationBarView",
    "NavigationBarItemView", "NavigationRailView", "TabLayout", "TabView", "TabItem",
    "NavigationView",  # the drawer list
)
_MIN_MEMBERS = 2


def _parse_bounds(s: Optional[str]):
    if not s:
        return None
    try:
        a, b = s.split("][")
        l, t = a.lstrip("[").split(",")
        r, bo = b.rstrip("]").split(",")
        return (int(l), int(t), int(r), int(bo))
    except (ValueError, AttributeError):
        return None


def _is_nav_class(cls: str) -> bool:
    tail = (cls or "").split(".")[-1]
    return any(n in cls for n in _NAV_CLASSES) or any(n == tail for n in _NAV_CLASSES)


def nav_container_members(xml: str) -> Set[Tuple[int, int, int, int]]:
    """Bounds of the clickable content-desc affordances that belong to a global-nav container.
    Empty when no container is confidently detected."""
    if not xml or not xml.strip():
        return set()
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return set()
    out: Set[Tuple[int, int, int, int]] = set()
    for container in root.iter("node"):
        kids = list(container)
        # candidate members: direct children that are clickable AND carry a content-desc
        members = []
        any_selected_attr = False
        for k in kids:
            if k.get("content-desc") and k.get("clickable") == "true":
                b = _parse_bounds(k.get("bounds"))
                if b is not None:
                    members.append((k, b))
            # the selected attribute (true OR false) on any sibling = the group exposes a
            # current-item signal -> a nav bar, not a plain content run
            sel = k.get("selected")
            if sel in ("true", "false") and k.get("content-desc"):
                any_selected_attr = True
            elif sel == "true" and k.get("content-desc"):
                any_selected_attr = True
        # the SELECTED (current) item is often clickable=false; count it as a sibling too so a
        # 4-tab bar with 1 selected still reads >=2 members + the selection signal
        selected_members = [
            (k, _parse_bounds(k.get("bounds"))) for k in kids
            if k.get("content-desc") and k.get("selected") == "true"
            and _parse_bounds(k.get("bounds")) is not None]
        group = members + [m for m in selected_members if m not in members]
        if len(group) < _MIN_MEMBERS:
            continue
        # POSITIVE DISCRIMINATOR: a nav container either exposes selection OR is a nav class.
        # Without one, a row of clickables (segmented control, filter chips, a content list run)
        # is NOT confidently global -> skip (conservative).
        container_nav = _is_nav_class(container.get("class", "")) or \
            any(_is_nav_class(k.get("class", "")) for k, _b in group)
        if not (any_selected_attr or container_nav):
            continue
        # EXCLUDE an adapter run-region: a scrollable container of many identical clickable rows
        # is a content list, not a nav bar. A nav bar's items are SHORT and side-by-side; a list
        # stacks tall rows. Heuristic: if the container is scrollable AND its members stack
        # vertically with tall (>=2x wide) rows, it is a list -> skip. A horizontal tab strip or
        # a wide-short bottom bar passes.
        if container.get("scrollable") == "true" and not container_nav:
            tall = sum(1 for _k, b in group if (b[3] - b[1]) > 1.5 * (b[2] - b[0]))
            if tall >= _MIN_MEMBERS:
                continue
        out.update(b for _k, b in group)
    return out


def nav_container_descs(xml: str) -> Set[str]:
    """The content-desc values of all detected global-nav container members (the section-switch
    affordances present on this screen). Used by routing to ask 'is the target's tab button here?'"""
    if not xml or not xml.strip():
        return set()
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return set()
    members = nav_container_members(xml)
    out: Set[str] = set()
    for el in root.iter("node"):
        b = _parse_bounds(el.get("bounds"))
        if b in members and el.get("content-desc"):
            out.add(el.get("content-desc"))
    return out


def in_nav_container(xml: str, node_bounds) -> bool:
    """Is the node at `node_bounds` a member of a detected global-nav container?"""
    nb = tuple(node_bounds) if node_bounds is not None else None
    return nb in nav_container_members(xml)
