"""The REFERENCE explorer — systematic BFS, scroll-aware, bounded; still NOT the product.

Proves the ingestion seam maps WHOLE apps without a human walk, and documents the contract a
smarter front-end (DroidRun / mobile-mcp class) replaces. Policy, app-agnostic and safe:

  * candidates = clickable, NON-checkable (a toggle is a state set, not navigation),
    non-IME, on-screen nodes of the settled snapshot;
  * BFS over discovered screens: each screen's untried candidates are worked breadth-first;
    REPOSITION ladder to reach a frontier screen: already-there -> navigate() (our own
    router — recorded edges, fork walks, anchors) -> bounded BACKs -> drop the screen
    (honest: an unreachable screen is skipped, never guessed at);
  * SCROLL-AWARE frontier: a settled screen with an adapter region is not exhausted at its
    first viewport — up to `max_scrolls_per_screen` content-advance swipes (the reveal
    machinery's own non-inverting span; COMMITTED through the ingester so the builder
    classifies them honestly: intra reveal / fork scroll edge / navigate) reveal more rows,
    which join the frontier;
  * Actions reported via the recorder's own selector ladder (narrowest-unique + borrow);
  * PACKAGE GUARD: an action that leaves the target package retreats immediately (the
    recorded edge is kept — it is real); volatile screens are crawled through, never
    expanded; budgets (max_actions, max_depth, per-screen scrolls) are hard."""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

from wendle.capture.selectors import borrow_descendant_selector, synthesize_selector
from wendle.capture.text_entry import is_ime_node
from wendle.fingerprint.signature import region_geometry
from wendle.models import Action, Selector
from wendle.reveal import _SWIPE_SECONDS, _advance_span


def _synth(node, frame_nodes):
    """The SAME selector ladder the human recorder uses (capture/recorder.py): narrowest-
    unique synthesis, then the labeled-descendant borrow for unlabeled clickable containers
    (the dominant Android row layout). One ladder, both front-ends — no coords-heavy crawls."""
    cx, cy = node.center
    sel, rep = synthesize_selector(node, center=(cx, cy), frame_nodes=frame_nodes)
    if rep == "coordinate_only":
        borrowed = borrow_descendant_selector(node, frame_nodes, cx, cy)
        if borrowed is not None:
            return borrowed
    return sel, rep


def _candidates(entered, nav_members=frozenset()):
    """Clickable, non-checkable, on-screen candidates — global-nav members (tab / bottom-nav /
    drawer buttons) FIRST so a tabbed app's every section is discovered before content
    exploration exhausts the budget (the global edges they mint also let reposition route back)."""
    nav, rest = [], []
    for n in entered.snapshot.nodes:
        if not n.clickable or n.checkable or is_ime_node(n):
            continue
        l, t, r, b = n.bounds
        if r - l <= 0 or b - t <= 0:
            continue
        (nav if tuple(n.bounds) in nav_members else rest).append(n)
    return nav + rest


def _key(sel) -> tuple:
    return (sel.kind, str(sel.value))


def _advance_gesture(xml, snapshot):
    """The next content-advance swipe for the screen's dominant region, with full geometry
    (start, end) so the builder can classify it — None when no region / degenerate span."""
    regions = region_geometry(xml)
    if not regions:
        return None
    region = max(regions, key=lambda r: (r["bounds"][2] - r["bounds"][0])
                 * (r["bounds"][3] - r["bounds"][1]))
    left, top, right, bottom = region["bounds"]
    root = snapshot.nodes[0].bounds if snapshot.nodes else None
    if region["axis"] == "y":
        cx = (left + right) // 2
        start, end = _advance_span(top, bottom, root[3] if root else None)
        if start == end:
            return None
        return (cx, start), (cx, end)
    cy = (top + bottom) // 2
    start, end = _advance_span(left, right, root[2] if root else None)
    if start == end:
        return None
    return (start, cy), (end, cy)


def _reposition(ingester, sid, *, sleep, clock, settle_pause, max_backs: int = 4) -> bool:
    """Get the device onto frontier screen `sid`: already-there -> navigate() -> bounded
    BACKs -> False (the caller drops the screen — skipped honestly, never guessed)."""
    if ingester.current_id == sid:
        return True
    from wendle.navigate.navigator import Navigator
    try:
        # clock/sleep THREADED THROUGH (no-blind-sleeps invariant): the router's waits run
        # on the crawl's injected time, so device-free tests stay wall-time-free.
        out = Navigator(ingester.graph, ingester.driver, dump_lock=ingester.lock,
                        settle_kwargs=ingester.settle_kwargs, clock=clock,
                        sleep=sleep).navigate(ingester.current_id, sid)
    except Exception:  # noqa: BLE001 — a router crash must not kill the crawl
        out = None
    ingester.reposition()
    if out is not None and out.status == "arrived" and ingester.current_id == sid:
        return True
    for _ in range(max_backs):
        if ingester.current_id == sid:
            return True
        ingester.driver.keyevent(4)
        sleep(settle_pause)
        ingester.reposition()
    return ingester.current_id == sid


def explore(ingester, package: str, *, max_actions: int = 12, max_depth: int = 2,
            max_scrolls_per_screen: int = 2, sleep=time.sleep, clock=time.monotonic,
            settle_pause: float = 0.8) -> dict:
    """BFS-crawl `package` from the CURRENT screen. Returns a value-free summary.
    The ingester must already be started (anchored) on an in-package screen."""
    tried: dict[str, set] = {}
    scrolls: dict[str, int] = {}
    depth_of: dict[str, int] = {}
    queue: deque = deque()
    actions = 0
    retreats = 0

    entered = ingester.observe()
    ingester.builder.begin(entered)
    root = ingester.current_id
    root_scr = ingester.graph.screen(root)
    if root_scr is not None and root_scr.force_action is None \
            and root_scr.package == package and root_scr.activity:
        # the crawl launched the app itself, so the root IS the launch surface — stamp the
        # am_start anchor (what reposition-by-navigate and later navigation re-anchor to)
        from wendle.models import ForceAction
        root_scr.force_action = ForceAction(
            "am_start", f"{root_scr.package}/{root_scr.activity}", verified_fp=root)
    depth_of[root] = 0
    queue.append(root)
    seen_screens = {root}

    def discover(sid: str, depth: int) -> None:
        if sid in seen_screens:
            return
        seen_screens.add(sid)
        depth_of[sid] = depth
        scr = ingester.graph.screen(sid)
        if scr is not None and scr.package == package and not scr.volatile \
                and depth <= max_depth:
            queue.append(sid)

    while queue and actions < max_actions:
        sid = queue.popleft()
        scr = ingester.graph.screen(sid)
        if scr is None or scr.volatile or depth_of.get(sid, 0) >= max_depth + 1:
            continue
        if not _reposition(ingester, sid, sleep=sleep, clock=clock,
                           settle_pause=settle_pause):
            retreats += 1
            continue  # unreachable frontier screen — skipped, not guessed
        entered = ingester.last_entered
        seen = tried.setdefault(sid, set())
        expand = depth_of.get(sid, 0) < max_depth
        while actions < max_actions:
            nxt = None
            if expand:
                from wendle.capture.affordance import nav_container_members
                nav_members = nav_container_members(ingester.builder._settled_xml or '')
                for n in _candidates(entered, nav_members):
                    sel, rep = _synth(n, entered.snapshot.nodes)
                    if _key(sel) in seen:
                        continue
                    nxt = (n, sel, rep)
                    break
            if nxt is None:
                # viewport exhausted: scroll-aware frontier — reveal more rows (bounded)
                if scrolls.get(sid, 0) < max_scrolls_per_screen \
                        and ingester.builder._settled_xml is not None:
                    g = _advance_gesture(ingester.builder._settled_xml, entered.snapshot)
                    if g is not None:
                        start, end = g
                        scrolls[sid] = scrolls.get(sid, 0) + 1
                        ingester.driver.swipe(start, end, _SWIPE_SECONDS)
                        sleep(settle_pause)
                        swipe = Action(selector=Selector("coords", start),
                                       action_type="swipe", end=end,
                                       replayability="coordinate_only")
                        ingester.commit(swipe, px=start[0], py=start[1], end=end,
                                        bounds=None)
                        actions += 1
                        cur = ingester.current_id
                        if cur != sid:
                            # the scroll forked/moved identity — the continuation screen
                            # inherits the SAME logical depth and frontier duty
                            discover(cur, depth_of.get(sid, 0))
                            tried.setdefault(cur, set()).update(seen)
                            sid = cur
                            seen = tried[sid]
                        entered = ingester.last_entered
                        continue
                break  # genuinely exhausted
            node, sel, rep = nxt
            seen.add(_key(sel))
            cx, cy = node.center
            ingester.driver.resolve_and_tap(Selector("coords", (cx, cy)), "click")
            sleep(settle_pause)
            action = Action(selector=sel, action_type="click", replayability=rep,
                            sensitive=False)
            before = ingester.current_id
            ingester.commit(action, bounds=node.bounds, px=cx, py=cy)
            actions += 1
            landed = ingester.current_id
            if landed != before:
                landed_scr = ingester.graph.screen(landed)
                if landed_scr is not None and landed_scr.package != package:
                    # package guard: the edge is real and kept; retreat, don't explore
                    ingester.driver.keyevent(4)
                    sleep(settle_pause)
                    ingester.reposition()
                    retreats += 1
                else:
                    discover(landed, depth_of.get(sid, 0) + 1)
                # return to working `sid` next loop turn via the reposition ladder
                if not _reposition(ingester, sid, sleep=sleep, clock=clock,
                           settle_pause=settle_pause):
                    retreats += 1
                    break  # cannot get back — move on to the next frontier screen
                entered = ingester.last_entered

    g = ingester.graph
    return {"actions": actions, "retreats": retreats,
            "screens": g.g.number_of_nodes(), "edges": g.g.number_of_edges()}
