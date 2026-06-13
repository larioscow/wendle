from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import networkx as nx

from wendle import actions
from wendle import reveal as _reveal
from wendle.fingerprint.compose import VIEW_PROFILE
from wendle.fingerprint.dumpsys import focused_package, foreground_namespace
from wendle.fingerprint.settle import settle
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.launch import LaunchLadder, LaunchResult
from wendle.capture.affordance import nav_container_descs
from wendle.navigate.affordance_verify import current_section, verify_by_affordance
from wendle.navigate.verify import Tier, config_for, observed_matches_id, verify_match

# DroidBot uses a fixed kill counter (~10) so a stuck policy can't loop forever; our
# closed loop re-observes and re-plans each iteration under the same bound.
STEP_CAP = 12
# Bounded restart-as-recovery (DroidBot relaunches the app as a universal anchor): how
# many times we may re-force an anchor before giving up.
MAX_RESTARTS = 3
_RETRY = object()  # global-affordance walk tapped but did not confirm -> caller re-observes


class NavStatus(str):
    """The stable, enumerated NavOutcome.status set — the honesty contract callers branch on,
    as importable constants instead of magic strings. (Plain `str` values, so they compare and
    serialize transparently and existing `NavOutcome("arrived")` call sites are unchanged.)"""

    ARRIVED = "arrived"                          # confirmed at the target (tier EXACT/STRUCTURE)
    ARRIVED_UNVERIFIED = "arrived_unverified"    # plausibly there but unconfirmable — caller decides
    OFF_GRAPH = "off_graph"                       # right app, screen has no route to the target
    CONTENT_DRIFT = "content_drift"               # on a known screen but the recorded selector is gone
    CROSS_APP_BOUNDARY = "cross_app_boundary"     # target/intermediate app has no anchor to reach it
    FORCE_FAILED = "force_failed"                 # an anchor force / launch ladder could not establish it
    NO_ROUTE = "no_route"                         # unreachable in the graph (topology), or no start
    COORDINATE_ONLY_REFUSED = "coordinate_only_refused"  # next step is a raw-coord tap and refusal is on
    CREDENTIAL_REQUIRED = "credential_required"   # a sensitive set_text needs a param not supplied

    @classmethod
    def all(cls) -> "set[str]":
        return {v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str)}


@dataclass
class NavOutcome:
    """Result of a navigate() attempt. The loop always STOPS AND REPORTS — it never
    guesses past an ambiguous state.

    status:
      arrived             reached the target, confirmed (tier EXACT or STRUCTURE).
      arrived_unverified  plausibly at the target but unconfirmable (WEAK, or a
                          content-free adapter-list screen) — caller decides.
      off_graph           in the right app but on a screen with no route to target.
      content_drift       on a known screen, but the recorded outbound selector is gone.
      cross_app_boundary  target (or a needed intermediate app) has no anchor to reach.
      force_failed        an anchor force could not establish the target app / ran out,
                          or the launch ladder exhausted every applicable rung.
      no_route            target unreachable in the graph (topology), or nothing to
                          start from.
      coordinate_only_refused  next step is a raw-coordinate tap and refusal is on.
      credential_required a sensitive set_text needs a param that wasn't supplied.
    """

    status: str
    tier: str = ""  # EXACT|STRUCTURE|WEAK when (un)arrived
    step_index: int = 0
    expected_id: str = ""
    observed_id: str = ""  # observed structure_id when off_graph
    observed_namespace: str = ""
    detail: str = ""
    # §3.7: when a content_drift came through the scroll-to-reveal rung, the TYPED
    # value-free report rides here (NavStatus itself stays a closed enum). None otherwise.
    reveal: Optional[object] = None


def _selector_sort(action) -> int:
    return {"resource_id": 0, "hint": 1, "label": 1, "text": 1, "content_desc": 1, "xpath": 1,
            "coords": 2}.get(
        action.selector.kind, 1
    )


def _best_edge(G, u: str, v: str):
    """Parallel-edge re-resolution (networkx#7582 is closed-as-intended): the weight fn
    sees the aggregated dict, so re-resolve the concrete min-weight edge DICT here, with a
    deterministic tiebreak so equal-weight parallel edges don't coin-flip."""
    edges = G[u][v]
    key = min(edges, key=lambda k: (edges[k]["weight"], _selector_sort(edges[k]["action"])))
    return edges[key]


def _best_action(G, u: str, v: str):
    return _best_edge(G, u, v)["action"]


def _wfn(u, v, d) -> float:
    return min(e.get("weight", 1.0) for e in d.values())


class Navigator:
    def __init__(
        self,
        graph: Graph,
        driver,
        *,
        params: Optional[Dict[str, str]] = None,
        dump_lock: Optional[threading.Lock] = None,
        refuse_coordinate_only: bool = True,
        settle_kwargs: Optional[dict] = None,
        replay_modes: Optional[Dict[str, str]] = None,
        clock=time.monotonic,
        sleep=time.sleep,
        ladder: Optional[LaunchLadder] = None,
    ):
        self.graph = graph
        self.driver = driver
        self.params = params or {}
        # Per-field text-entry replay override (plain JSON data, no callables), keyed by a
        # field's selector value OR its {param}/field name -> 'per_key' | 'atomic'. For the
        # reactive fields the recorder can't detect (Compose/WebView no-ops, backend-latency
        # reactions). Override > the Action's recorded replay_mode > 'atomic' default.
        self.replay_modes = replay_modes or {}
        self.lock = dump_lock or threading.Lock()
        self.refuse_coordinate_only = refuse_coordinate_only
        self.settle_kwargs = settle_kwargs or {}
        self.clock = clock
        self.sleep = sleep
        # ONE launch ladder behind ONE verify gate — the same rungs the replay engine uses.
        # `lambda: self._observe()` resolves at call time, so a test that patches _observe
        # after construction is honored (same trick as the engine).
        self._ladder = ladder or LaunchLadder(
            graph, driver, lambda: self._observe(), clock=clock, sleep=sleep)

    # ---- observation ----
    def _observe(self) -> Tuple[str, str, Optional[str], bool]:
        box = {}

        def dump_fn():
            with self.lock:
                return self.driver.dump_hierarchy()

        def ns_fn():
            with self.lock:
                act, win = self.driver.dumps()
            box["f"] = focused_package(win)
            return foreground_namespace(act, win)

        xml, ns, settled_ok = settle(
            dump_fn, ns_fn, lambda _x: VIEW_PROFILE, focus_fn=lambda: box.get("f"),
            **self.settle_kwargs,
        )
        return xml, ns, box.get("f"), settled_ok

    def _actual_node(self, xml: str, ns: str, focus: Optional[str], G,
                     route_to: Optional[str] = None, strict: bool = False) -> Optional[str]:
        """Which graph node are we actually on? EXACT fingerprint first (strongest, and
        unique), then the text-free STRUCTURE tier. None when the screen is off-graph.

        A STRUCTURE collision (two screens sharing a widget skeleton — common for single-
        Activity / Compose-nav apps) is resolved DETERMINISTICALLY (never by iteration
        order, review #1/#2/#4): among the matching twins, prefer one that has a recorded
        routable path TO `route_to` — i.e. a real intermediate we can navigate from — so a
        twin intermediate is still walked toward the target (review-2 #A). Only when no
        twin can route closer do we fall back to the target itself (then the arrival gate
        reports it honestly as arrived_unverified, never a confident wrong arrival).

        `strict` is the HOOK-KEYING mode (the engine's boundaries): return a node ONLY when
        the observation pins it as hard as navigate()'s own arrival gate demands — an EXACT
        match no twin could carry, or a sole structure candidate that is fingerprint-
        unambiguous. Anything weaker returns None: injected developer code must never bind
        to a guessed twin. Residual (identical to the navigator's): an UNRECORDED physical
        twin of a text-free screen is undetectable. Default False — routing keeps the
        best-guess-twin behavior above; ONLY the engine's _fire_phase passes True."""
        def _exact(sid):
            s = self.graph.screen(sid)
            # refinement-aware (task #17b): a refined twin matches when the live coarse fingerprint
            # + live chrome digest reproduce its refined id — so the RIGHT sibling page is picked
            # by its chrome, not guessed among structure twins.
            return s is not None and observed_matches_id(ns, xml, s, focus, config_for(s))

        for sid in G.nodes:  # EXACT ids are unique — first (only) match is unambiguous
            if _exact(sid):
                if strict and self._fingerprint_ambiguous(self.graph.screen(sid), G):
                    return None  # a volatile/structural sibling could carry this id — never guess
                return sid

        obs_struct = structure_id(ns, xml, focus)

        def _struct(sid):
            s = self.graph.screen(sid)
            return s is not None and s.structure_id and s.structure_id == obs_struct

        cands = [sid for sid in G.nodes if _struct(sid)]
        if not cands:
            return None
        if strict:
            if len(cands) > 1:
                return None  # structure twins: cannot tell which we are on
            only = self.graph.screen(cands[0])
            if self._fingerprint_ambiguous(only, G) or self._structure_ambiguous(only, G):
                return None
            return cands[0]
        if route_to is not None and len(cands) > 1:
            routable = []
            for c in cands:
                if c == route_to:
                    continue
                try:
                    d = nx.dijkstra_path_length(G, c, route_to, weight=_wfn)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                routable.append((d, c))
            if routable:
                routable.sort()  # nearest routable twin (then id) — deterministic
                return routable[0][1]
        if route_to in cands:
            return route_to
        return sorted(cands)[0]

    def _structure_ambiguous(self, target, G) -> bool:
        """More than one graph node carries the target's structure_id — a STRUCTURE-only
        match can't tell which twin we're on, so arrival must be reported unverified."""
        sid = target.structure_id
        if not sid:
            return False
        seen = 0
        for nid in G.nodes:
            s = self.graph.screen(nid)
            if s is not None and s.structure_id == sid:
                seen += 1
                if seen > 1:
                    return True
        return False

    def _value_bearing_on_sight(self, target, xml, focus) -> bool:
        """Is an EXACT match confident WITHOUT corroboration? True when the id folds in on-screen
        VALUES no unrecorded twin could reproduce: a Compose text-bearing id always; a refined
        twin (task #17b) UNLESS it was RECORDED adapter-list-dominant (its digest reduces to the
        toolbar title, which a same-titled unrecorded sibling could carry — HW2). Keyed on the
        target's RECORDED `adapter_dominant`, NOT the live observed row count — an empty/sparse
        list at navigate would else flip adapter_list_dominant False and bypass the guard
        (adversarial finding: the cardinal-sin confident-wrong arrival)."""
        if config_for(target).include_text and target.value_bearing is True:
            # L3 (lazy-region §2.5): include_text alone proves nothing once in-region values
            # are suppressed — confidence requires the RECORDED fact that outside-region values
            # actually entered the hash. None/False (incl. legacy) -> corroboration path.
            return True
        if target.coarse_id is not None:
            return not target.adapter_dominant
        return False

    def _fingerprint_ambiguous(self, target, G) -> bool:
        """A fingerprint (EXACT id) match is confident ONLY when no OTHER screen could carry
        that same fingerprint. This is PROFILE-AWARE because the id's discriminating power
        depends on whether the target's fingerprint folds in on-screen text:

        - TEXT-BEARING target (Compose, include_text=True): the id hashes the visible text, so
          a text-free twin can never reproduce it and a different-text sibling gets a different
          id -> EXACT is genuinely unique -> NOT ambiguous (don't downgrade a real arrival).
        - TEXT-FREE target (view/volatile, include_text=False): the id IS structure-deep, so it
          is shared by EITHER (a) any other node carrying the same structure_id — including a
          SETTLED cross-profile twin whose text the view profile drops — OR (b) any VOLATILE
          node in the same activity, which may SETTLE into the target's skeleton (a volatile
          node's RECORDED structure_id is taken from its unsettled, possibly spinner-bearing
          tree and can differ from what it settles to, so a structure_id match is NOT required
          to be a threat). Either makes the EXACT match non-unique -> ambiguous.
        """
        sid = target.structure_id
        if not sid:
            return False
        if target.coarse_id is not None:
            return False  # a REFINED twin's id folds in its chrome values -> unique in the graph,
            #               the same value-bearing argument that exempts a Compose text id (task #17b)
        if config_for(target).include_text and target.value_bearing is True:
            return False  # genuinely value-bearing fingerprint -> unique; no twin can carry it.
            # value_bearing None/False: after in-region suppression the id is effectively
            # text-free — fall through to the text-free twin scan (L3, lazy-region §2.5).
        for nid in G.nodes:
            s = self.graph.screen(nid)
            if s is None or s.id == target.id:
                continue
            if s.structure_id == sid:  # settled OR volatile structure twin -> same text-free id
                return True
            if s.volatile and s.namespace == target.namespace:  # could settle into the target
                return True
        return False

    # ---- forcing / anchors ----
    def _force(self, fa) -> LaunchResult:
        """Force an anchor via the ONE shared LaunchLadder (recorded component -> recorded
        icon tap -> package default -> monkey, behind the one verify_foreground gate) — the
        IconTap reach is what makes shared-package launcher entries (Gemini in the Google app)
        reachable at all. The return contract is the ladder's: landed (gated, carrying the
        gate's own observation for the loop to consume), deferred (HomePress — readiness is
        handed to the next step), or exhausted (landed=False), which the loop maps to an
        honest force_failed instead of silently re-forcing a broken launch."""
        if fa is None:
            return LaunchResult(landed=False, error="no force action")
        return self._ladder.launch(fa)

    def _anchor_in_pkg(self, pkg: Optional[str]):
        """The nearest verified anchor whose screen is IN `pkg` — never a foreign-app
        anchor (Failure 3: seeding from a wrong-package anchor causes route ping-pong)."""
        if not pkg:
            return None
        for sid in self.graph.anchors():
            s = self.graph.screen(sid)
            if s is not None and s.package == pkg:
                return s
        return None

    def _seed_anchor(self, target, G=None):
        """Where to force first: an anchor in the target's app, else the target itself if it
        is an anchor, else an anchor FROM WHICH THE TARGET IS RECORDED-REACHABLE (the
        anchorless cross-package shape — an OEM/share/OAuth page entered only by a recorded
        edge from another app, e.g. Samsung Settings->Battery: the Settings anchor routes
        there, the launcher anchor does not), else any anchor (a controlled state)."""
        a = self._anchor_in_pkg(target.package)
        if a is not None:
            return a
        if target.force_action is not None and target.force_action.verified:
            return target
        anchors = self.graph.anchors()
        if G is not None and target.id in G:
            for sid in anchors:
                if sid in G and nx.has_path(G, sid, target.id):
                    return self.graph.screen(sid)
        return self.graph.screen(anchors[0]) if anchors else None

    def _resolve_replay_mode(self, action) -> str:
        key = action.selector.value
        if isinstance(key, str) and key in self.replay_modes:
            return self.replay_modes[key]
        field = (action.value or {}).get("param") or (action.value or {}).get("field")
        if field and field in self.replay_modes:
            return self.replay_modes[field]
        return (action.value or {}).get("replay_mode", "atomic")

    # ---- step execution (via the shared ActionExecutor) ----
    def _execute(self, action) -> actions.ActionResult:
        """Run one action under the navigator's policy: refuse coordinate-only taps (a fragile,
        element-less bind), type atomically (per_key for a reactive field), and do NOT verify text
        — the closed loop re-observes SCREEN ARRIVAL each step, so a silent text no-op surfaces later
        as an honest content_drift/off_graph stop (it is not a per-step text-landing check). Routing
        every action through the shared executor also gives the navigator the swipe + keyevent
        handlers it used to lack — a routed swipe edge now SWIPES rather than tapping the element
        center (a confident-wrong action)."""
        ctx = actions.ActionContext(
            self.driver, params=self.params, reproduce_coords=not self.refuse_coordinate_only,
            faithful_text=False, verify_text=False, resolve_mode=self._resolve_replay_mode)
        return actions.execute(action, ctx)

    def _walk_scroll_edge(self, u, v, scroll_action, xml, ns, focus, G, step, to_id):
        """Walk a chrome-fork SCROLL edge u->v (Cap 1). Returns None on success (the device was
        scrolled toward v; the caller re-observes and its arrival gate decides) or a typed
        NavOutcome on an honest stop. NEVER grants corroboration — a coordinate scroll proves
        nothing; only a recorded-SELECTOR edge or a gated launch earns trust.

        L6 departure gate: walk ONLY when the LIVE observation EXACT-reproduces the edge's
        SOURCE id (never namespace-trust or a best-guess twin — a scroll mutates state). Honest
        under-claim: a post-launch namespace-trusted T_top that never EXACT-reproduces gets no
        walk -> off_graph; the fix covers only EXACT-verifiable sources."""
        src = self.graph.screen(u)
        if src is None or not observed_matches_id(ns, xml, src, focus, config_for(src)):
            return NavOutcome("off_graph", step_index=step, expected_id=to_id,
                              observed_id=structure_id(ns, xml, focus), observed_namespace=ns,
                              detail="fork-walk source not exact-verified")
        tgt = self.graph.screen(v)

        def resolves(xml_, ns_, focus_) -> bool:
            # the strict, target-keyed arrival-equivalent gate (NOT the best-guess _actual_node):
            # the live screen reproduces v AND v is genuinely unambiguous in the graph.
            return (tgt is not None
                    and observed_matches_id(ns_, xml_, tgt, focus_, config_for(tgt))
                    and not self._fingerprint_ambiguous(tgt, G)
                    and not self._structure_ambiguous(tgt, G))

        def recognized_other(xml_, ns_, focus_) -> bool:
            # FOREIGN means a known screen that is NEITHER the target NOR the source: still
            # observing the (L6-verified) source just means the swipe hasn't flipped the fork
            # yet — keep advancing (budget/no-movement bound it); aborting there would name an
            # inference the dump contradicts (L4) and strand a route a few swipes from done.
            sid = self._actual_node(xml_, ns_, focus_, G, strict=True)
            return sid is not None and sid not in (v, u)

        report = _reveal.walk_to_node(
            self.driver, scroll_action, src, self._observe,
            resolves=resolves, recognized_other=recognized_other,
            clock=self.clock, sleep=self.sleep)
        if report.reason == _reveal.REVEALED:
            return None  # reached v's vicinity — the loop's arrival gate makes the real call
        # observed_id deliberately EMPTY: v was precisely what the walk failed to observe, and
        # the convention everywhere else is observed_id = what the dump actually showed.
        out = NavOutcome("content_drift", step_index=step, expected_id=to_id,
                         detail=f"fork-walk {report.reason} after {report.steps} step(s)")
        out.reveal = report
        return out

    def _affordance_recovery(self, to_id, focus, tried: set):
        """A TARGET-inbound NAV affordance (a tab / bottom-nav / drawer button) not yet tried
        this call, for the GLOBAL-chrome recovery rung. Restricted to in-app, tap-class,
        STABLE-selector inbound edges — the recorded ways INTO the target. Returns the Action
        to attempt (the caller _executes it and lets the arrival gate judge) or None. Honesty:
        every attempt is verified by the normal arrival gate; a wrong tap is an honest non-
        arrival, never a confident landing — so the worst case is a bounded wasted tap."""
        target = self.graph.screen(to_id)
        if target is None or (focus and target.package and focus != target.package):
            return None  # only inside the target's own app (the affordance won't resolve else)
        for u, v, k in self.graph.g.in_edges(to_id, keys=True):
            data = self.graph.g[u][v][k]
            a = data.get("action")
            if a is None or data.get("action_class") == "scroll":
                continue
            if a.action_type not in ("click", "long_click"):
                continue
            if a.selector.kind not in ("content_desc", "resource_id", "label", "text", "hint"):
                continue  # coords/swipe are not global chrome
            key = (a.selector.kind, a.selector.value)
            if key in tried:
                continue
            tried.add(key)
            return a
        return None

    def _global_affordance_action(self, to_id, xml, tried):
        """A GLOBAL_AFFORDANCE inbound edge of to_id whose affordance is PRESENT in a nav
        container on the current xml and whose handle is untried this call. None otherwise.
        First-class global edge: usable from ANY in-app screen showing the target's tab button."""
        here = nav_container_descs(xml)
        for u, v, k in self.graph.g.in_edges(to_id, keys=True):
            data = self.graph.g[u][v][k]
            if not data.get("global_affordance"):
                continue
            a = data.get("action")
            if a is None or a.action_type != "click":
                continue
            if (a.selector.kind, a.selector.value) in tried:
                continue
            if a.selector.value in here:  # the section's own tab button is on THIS screen
                return a
        return None

    def _walk_global_affordance(self, to_id, focus, xml, tried):
        """Reach to_id by its GLOBAL-NAV affordance, content-independently (a content-drifting
        tab whose fingerprint matches nothing). Returns a TERMINAL NavOutcome (arrived /
        arrived_unverified, tier='AFFORDANCE' — which can NEVER reach the EXACT branch), the
        _RETRY sentinel if it tapped without confirming (caller re-observes; bounded by the
        handle-keyed tried-set), or None if no global affordance applies. Two cases:
          (a) ALREADY on the target's section — a normal edge or cold launch put us on a
              SETTLED screen whose target tab is selected -> arrived, no tap;
          (b) NOT there — tap the target's tab to SWITCH, then verify-by-affordance (which
              additionally requires the content to have NAVIGATED, closing the sticky-drawer /
              optimistic-selection paths)."""
        target = self.graph.screen(to_id)
        if target is None or not (focus and target.package and focus == target.package):
            return None
        aff_val = self._target_global_affordance_value(to_id)
        if aff_val is None:
            return None
        # (a) already on the section? (the loop observed a SETTLED frame this iteration).
        # Confident only for a SECTION LANDING; a deeper in-section screen -> unverified.
        if current_section(xml, aff_val, focus, target.package) == "yes":
            if self._is_section_landing(to_id):
                return NavOutcome("arrived", tier="AFFORDANCE", expected_id=to_id)
            return NavOutcome("arrived_unverified", tier="AFFORDANCE", expected_id=to_id)
        # (b) switch to it: tap the target's tab (present + untried), confirm by content-change
        aff = self._global_affordance_action(to_id, xml, tried)
        if aff is None:
            return None
        tried.add((aff.selector.kind, aff.selector.value))
        pre_xml = xml
        res = self._execute(aff)
        if not res.ok:
            return _RETRY
        px, pns, pfocus, _ = self._observe()  # SETTLED post-tap observation (no mid-transition read)
        verdict = verify_by_affordance(pre_xml, px, aff.selector.value, pfocus, target.package)
        if verdict == "arrived":
            return NavOutcome("arrived", tier="AFFORDANCE", expected_id=to_id)
        if verdict == "unverified":
            return NavOutcome("arrived_unverified", tier="AFFORDANCE", expected_id=to_id)
        return _RETRY  # 'no' — tapped but did not reach; re-plan (handle now in tried)

    def _is_section_landing(self, to_id) -> bool:
        """Is to_id a global-nav SECTION LANDING — a screen reached ONLY via a tab/nav button
        (every inbound edge is a global affordance)? A landing IS the section, so 'the section
        is active' confidently means 'on this screen'. A deeper in-section screen has a NON-
        global (content) inbound edge, so the tab signal cannot pin it -> not a confident
        landing. A node with no inbound edges (e.g. the anchor's own launch tab) is a landing."""
        for u, v, k in self.graph.g.in_edges(to_id, keys=True):
            if not self.graph.g[u][v][k].get("global_affordance"):
                return False
        return True

    def _target_global_affordance_value(self, to_id):
        """The content-desc/value of to_id's recorded inbound GLOBAL_AFFORDANCE edge (the tab
        that opens it), or None. Used to ask 'is the target's section currently active'."""
        for u, v, k in self.graph.g.in_edges(to_id, keys=True):
            data = self.graph.g[u][v][k]
            if data.get("global_affordance") and data.get("action") is not None:
                return data["action"].selector.value
        return None

    def _action_outcome(self, res, step: int, expected_id: str, actual: str) -> NavOutcome:
        """Map a failed ActionResult to a NavOutcome by its TYPED reason (no substring-matching)."""
        if res.reason == actions.CREDENTIAL_REQUIRED:
            return NavOutcome("credential_required", step_index=step, expected_id=expected_id, detail=res.error)
        if res.reason in (actions.COORDINATE_REFUSED, actions.UNSUPPORTED):
            return NavOutcome("coordinate_only_refused", step_index=step, expected_id=expected_id, detail=res.error)
        # NOT_RESOLVED / TEXT_NOT_LANDED / CHECKBOX_DRIFT — the recorded selector/action drifted.
        return NavOutcome("content_drift", step_index=step, expected_id=expected_id,
                          observed_id=actual, detail=res.error or "recorded selector did not resolve")

    # ---- the closed loop ----
    def navigate(self, from_id: str, to_id: str) -> NavOutcome:
        from wendle.graph import check_signature_version
        check_signature_version(self.graph)  # stale ids -> typed instant refusal (§2.6)
        # `from_id` is advisory — the loop re-observes the true state every step.
        target = self.graph.screen(to_id)
        if target is None or to_id not in self.graph.g.nodes:
            return NavOutcome("no_route", expected_id=to_id, detail="target not in graph")
        G = self.graph.routable_subgraph()
        seed = self._seed_anchor(target, G)
        if seed is None:
            return NavOutcome("no_route", expected_id=to_id, detail="no anchor to start from")
        # OBSERVE-FIRST: forcing is RECOVERY (DroidBot's restart-as-recovery), never a
        # prologue — navigating within an app we are already inside must not cold-stop it.
        # Namespace-trust is only ever granted AFTER a gated/deferred force (a bare "we share
        # the seed's namespace" first contact proves only "somewhere in this activity" — on a
        # single-Activity app that is ANY screen, and firing recorded actions there is a
        # confident-wrong action; adversarial finding).
        forced = None   # the anchor we last forced — trusted by namespace until recognized
        pending = None  # the launch gate's own observation: consume it instead of re-observing
        # CORROBORATION: confident arrival on TEXT-FREE evidence (a structure-strength match)
        # requires verified interaction in THIS call — a gated/deferred launch or a walked
        # recorded edge. Without it, the match could equally be an UNRECORDED same-namespace
        # twin (Inbox vs Archive); the old prologue-force provided this implicitly.
        corroborated = False
        # §2.8 (lazy-region design): the TARGET node carries a record-time suspect self-loop
        # (a tap that collapsed a region and landed on a logically-different screen aliasing to
        # the same id — an invisible navigation). Its id is known-ambiguous, so arrival there is
        # NEVER confident (on-sight included) — only ever arrived_unverified. Keyed on the NODE,
        # not a walked edge: the suspect edge is a self-loop, which routable_subgraph drops, so
        # an edge-keyed guard could never fire on a real recording.
        suspect_target = self.graph.has_suspect_self_loop(to_id)
        # Cap 1: a §2.8-suspect node walked THROUGH as a fork-walk waypoint is known-ambiguous;
        # it caps every downstream confidence claim of THIS navigate() call at arrived_unverified
        # (mirrors suspect_target, extended to walked waypoints).
        suspect_waypoint = False
        walked_scroll: dict = {}  # (u, v) -> walk count this call (the fork-walk liveness cap)
        tried_affordances: set = set()  # (kind, value) global-nav buttons tried this call

        def _recover(anchor_screen, step):
            """One gated re-anchor via the ladder. Returns (failure, observation, trust):
            landed/deferred -> (None, gate_obs_or_None, anchor_screen)  [namespace-trust it];
            exhausted but the post-exhaust world is a RECOGNIZED graph node -> (None, obs,
            None) — the rungs routinely leave the device on a REAL screen (an anchor whose
            recorded namespace drifted: logged-out Login vs logged-in app), and the closed
            loop, not the launch, is the authority: keep routing, grant NO trust;
            exhausted on an unrecognized world -> (honest force_failed, None, None)."""
            res = self._force(anchor_screen.force_action)
            if res.landed:
                return None, res.observation, anchor_screen
            xml2, ns2, focus2, _ = obs2 = self._observe()
            if self._actual_node(xml2, ns2, focus2, G, route_to=to_id) is not None:
                return None, obs2, None
            return NavOutcome("force_failed", step_index=step, expected_id=to_id,
                              observed_namespace=ns2,
                              detail=f"launch ladder exhausted: {res.error}"), None, None

        prev_remaining: Optional[int] = None
        stale = 0
        restarts = 0
        for step in range(STEP_CAP):
            if pending is not None:
                xml, ns, focus, settled_obs = pending
                pending = None
            else:
                xml, ns, focus, settled_obs = self._observe()
            actual = self._actual_node(xml, ns, focus, G, route_to=to_id)
            # Trust a just-forced anchor by namespace (DroidBot restart-trust): am_start
            # LANDED us on this app's launch screen, so even a heavily dynamic home whose
            # skeleton won't reproduce (e.g. Instagram's feed) is still that anchor — route
            # from it instead of demanding a structural match and relaunching the app.
            if actual is None and forced is not None and focus and \
                    focus == forced.package and ns == forced.namespace:
                actual = forced.id
            elif actual is not None:
                forced = None  # recognized a concrete screen — stop blind-trusting
            tier = verify_match(xml, ns, target, self.driver, focus)

            # arrival
            if actual == to_id:
                # EXACT is confident ONLY when its id is genuinely unique — NOT when a volatile
                # structural twin could carry the same fingerprint (the false 'arrived EXACT' on
                # a launch/welcome screen that is a twin of the target). A STRUCTURE match is
                # confident ONLY when the skeleton is unique in the graph. Otherwise we can't
                # tell which twin we're on -> report it honestly as arrived_unverified.
                # Confident arrival requires the target be PROVABLY unique. _fingerprint_ambiguous
                # is the profile-aware test (text-bearing id -> unique; text-free id -> shared by
                # any structure twin OR any volatile same-activity node that could SETTLE into it).
                # It gates BOTH tiers — a skeleton-drift volatile twin defeats the structure-only
                # guard, so STRUCTURE must honor it too; STRUCTURE additionally needs a unique
                # recorded structure_id.
                ambiguous = self._fingerprint_ambiguous(target, G)
                exact_ok = tier == Tier.EXACT and not ambiguous
                struct_ok = (tier >= Tier.STRUCTURE and not ambiguous
                             and not self._structure_ambiguous(target, G))
                if exact_ok or struct_ok:
                    # a TEXT-BEARING EXACT id folds the visible text in — no twin can carry
                    # it, so it is confident on sight. TEXT-FREE evidence (view-profile EXACT
                    # — which equals STRUCTURE in discriminating power — or any STRUCTURE
                    # match) could equally be an UNRECORDED same-namespace twin, so it is
                    # confident only when CORROBORATED by verified interaction this call.
                    # A REFINED twin (task #17b) carries chrome VALUE evidence, so it is also
                    # confident on sight — EXCEPT on an adapter-list-dominant page, where the
                    # digest reduces to the title and a same-titled unrecorded sibling could
                    # collide (HW2): there it falls back to corroboration like text-free evidence.
                    suspect = suspect_target or suspect_waypoint
                    if exact_ok and not suspect and \
                            self._value_bearing_on_sight(target, xml, focus):
                        return NavOutcome("arrived", tier="EXACT", expected_id=to_id)
                    if corroborated and not suspect:
                        return NavOutcome("arrived", tier="EXACT" if exact_ok else "STRUCTURE",
                                          expected_id=to_id)
                    # zero-corroboration text-free match: VERIFY by re-anchoring and walking
                    # (bounded, old-loop style) before claiming; if we can't, report honestly.
                    if restarts < MAX_RESTARTS:
                        restarts += 1
                        fail, pending, trust = _recover(seed, step)
                        if fail is None:
                            forced = trust
                            corroborated = corroborated or trust is not None
                            continue
                return NavOutcome("arrived_unverified", tier=Tier(max(tier, Tier.UNVERIFIABLE)).name,
                                  expected_id=to_id)

            # GLOBAL-AFFORDANCE arrival (content-independent): the TARGET's own nav section is
            # the ACTIVE one on this SETTLED screen — its tab is selected. Reaches a content-
            # drifting tab whose fingerprint matched a sibling twin (Teabc118) or nothing, so
            # `actual` never equals the recorded target id. Confirmed by the affordance, NEVER
            # a fingerprint -> tier AFFORDANCE (a separate terminal that can never be EXACT).
            # Only fires for a target whose own INBOUND edge is a global affordance (a tab
            # screen); a deep screen reached FROM a tab has no global inbound edge -> skipped.
            if settled_obs and focus and target.package and focus == target.package:
                _aff_val = self._target_global_affordance_value(to_id)
                if _aff_val is not None and \
                        current_section(xml, _aff_val, focus, target.package) == "yes":
                    # the affordance proves only the SECTION is active. Confidently arrive ONLY
                    # if to_id is a SECTION LANDING — a screen reached ONLY via tabs (no non-
                    # global inbound edge). A DEEPER screen in the section (reached by a content
                    # tap) is indistinguishable from the landing by the tab signal alone, so it
                    # is honestly arrived_unverified, never a confident landing on the wrong node.
                    if self._is_section_landing(to_id):
                        return NavOutcome("arrived", tier="AFFORDANCE", expected_id=to_id)
                    return NavOutcome("arrived_unverified", tier="AFFORDANCE", expected_id=to_id)

            # an uncorroborated first-contact WEAK is not a give-up signal: fall through to
            # the recovery ladder (re-anchor + walk) and only report WEAK once we have tried.
            if tier == Tier.WEAK and actual is None and (corroborated or restarts > 0):
                return NavOutcome("arrived_unverified", tier="WEAK", expected_id=to_id)

            # wrong app: NOT inherently lost. A recognized node with a RECORDED route to the
            # target (possibly through a kept cross-package edge — the share/OAuth/OEM-page
            # shape, e.g. Settings->Battery on Samsung) walks that route like any selector
            # edge: fall through to the planner. Re-anchoring the target app (or the typed
            # cross_app_boundary refusal when it has no anchor) is the FALLBACK for an
            # unrecognized screen or a route-less node — never the first resort.
            has_recorded_route = (actual is not None and actual in G and to_id in G
                                  and nx.has_path(G, actual, to_id))
            if focus and target.package and focus != target.package and not has_recorded_route:
                a = self._anchor_in_pkg(target.package)
                if a is None:
                    # no anchor IN the target app — but the SEED may still reach it over a
                    # recorded cross-package edge (the anchorless OEM/share-target shape:
                    # Settings->Battery). Re-anchor at the seed and walk the recorded route;
                    # refuse typed only when NEITHER an anchor nor a seed-route exists.
                    if seed.id in G and to_id in G and nx.has_path(G, seed.id, to_id) \
                            and restarts < MAX_RESTARTS:
                        restarts += 1
                        fail, pending, trust = _recover(seed, step)
                        if fail is not None:
                            return fail
                        forced = trust
                        corroborated = corroborated or trust is not None
                        continue
                    return NavOutcome("cross_app_boundary", step_index=step, expected_id=to_id,
                                      observed_namespace=ns, detail="target app has no anchor")
                if restarts >= MAX_RESTARTS:
                    return NavOutcome("force_failed", step_index=step, expected_id=to_id,
                                      observed_namespace=ns, detail="anchor did not land target app")
                restarts += 1
                fail, pending, trust = _recover(a, step)
                if fail is not None:
                    return fail
                forced = trust
                corroborated = corroborated or trust is not None
                continue

            # right app but unrecognized screen. FIRST try a GLOBAL-NAV affordance into the
            # target (a content-drifting tab whose fingerprint matches nothing is reached by
            # tapping its tab button + VERIFY-BY-AFFORDANCE) before falling back to re-anchor.
            if actual is None and focus and target.package and focus == target.package:
                ga = self._walk_global_affordance(to_id, focus, xml, tried_affordances)
                if isinstance(ga, NavOutcome):
                    return ga
                if ga is _RETRY:
                    continue
            # snap back to the seed anchor (bounded), else honestly report off_graph
            if actual is None:
                if restarts < MAX_RESTARTS:
                    restarts += 1
                    fail, pending, trust = _recover(seed, step)
                    if fail is not None:
                        return fail
                    forced = trust
                    corroborated = corroborated or trust is not None
                    continue
                return NavOutcome("off_graph", step_index=step, expected_id=to_id,
                                  observed_id=structure_id(ns, xml, focus), observed_namespace=ns,
                                  detail="screen not in graph")

            # plan from where we ACTUALLY are (never from a foreign anchor)
            try:
                _dist, path = nx.multi_source_dijkstra(G, {actual}, to_id, weight=_wfn)
            except (nx.NetworkXNoPath, KeyError):
                # no recorded route from HERE — FIRST try a GLOBAL-NAV affordance into the
                # target (its tab button is present on this in-app screen), confirmed by
                # VERIFY-BY-AFFORDANCE; then re-anchor at the seed before honestly reporting.
                ga = self._walk_global_affordance(to_id, focus, xml, tried_affordances)
                if isinstance(ga, NavOutcome):
                    return ga
                if ga is _RETRY:
                    continue
                # re-anchor at the seed (from which the target
                # was reachable when recorded) before honestly reporting off_graph. Forcing
                # used to be the loop's blind prologue, which provided this by accident;
                # observe-first makes it an explicit recovery rung. Re-anchoring where we
                # already ARE can't open a route, so that is off_graph immediately.
                if actual != seed.id and restarts < MAX_RESTARTS:
                    restarts += 1
                    fail, pending, trust = _recover(seed, step)
                    if fail is not None:
                        return fail
                    forced = trust
                    corroborated = corroborated or trust is not None
                    continue
                # GLOBAL-AFFORDANCE RECOVERY (single-Activity tabbed / bottom-nav / drawer
                # apps): no recorded route from here, but the TARGET's own recorded inbound
                # nav affordance (a tab / bottom-nav / drawer button) may be GLOBAL chrome
                # present on THIS screen. Try it; the loop's arrival gate then judges the
                # result on a FRESH observation — a wrong attempt lands a non-target screen
                # and re-plans (bounded by the tried-set + STEP_CAP), NEVER a confident
                # landing. Does NOT originate corroboration (a value-bearing tab arrives
                # on-sight; a text-free one is honestly arrived_unverified).
                aff = self._affordance_recovery(to_id, focus, tried_affordances)
                if aff is not None and self._execute(aff).ok:
                    continue
                return NavOutcome("off_graph", step_index=step, expected_id=to_id,
                                  observed_id=structure_id(ns, xml, focus), observed_namespace=ns,
                                  detail="no route from current screen")
            if len(path) < 2:
                return NavOutcome("arrived_unverified", tier=Tier(max(tier, Tier.UNVERIFIABLE)).name,
                                  expected_id=to_id)

            # progress debounce: two consecutive non-improving plans -> we're stuck
            remaining = len(path)
            if prev_remaining is not None and remaining >= prev_remaining:
                stale += 1
                if stale >= 2:
                    return NavOutcome("off_graph", step_index=step, expected_id=to_id,
                                      observed_id=structure_id(ns, xml, focus),
                                      observed_namespace=ns, detail="not getting closer")
            else:
                stale = 0
            prev_remaining = remaining

            u, v = path[0], path[1]
            edge = _best_edge(G, u, v)
            if edge.get("action_class") == "scroll":
                # Cap 1: a chrome-fork continuation. WALK it (scroll the device toward the
                # forked twin) rather than execute it as an action. The walk NEVER originates
                # corroboration — it only moves the device; arrival is decided by this loop's
                # full gate on the next observation.
                # LIVENESS CAP: a walk iteration is ~30x heavier than a tap (up to 30 real
                # swipes), and an alternating walk-forward/bounce-back cycle resets the
                # path-length debounce each bounce — so each scroll PAIR may be walked at
                # most twice per navigate() call; a third re-plan is "not getting closer".
                walked_scroll[(u, v)] = walked_scroll.get((u, v), 0) + 1
                if walked_scroll[(u, v)] > 2:
                    return NavOutcome("off_graph", step_index=step, expected_id=to_id,
                                      observed_id=structure_id(ns, xml, focus),
                                      observed_namespace=ns,
                                      detail="fork-walk re-planned repeatedly — not getting closer")
                out = self._walk_scroll_edge(u, v, edge["action"], xml, ns, focus, G, step, to_id)
                if out is not None:
                    return out  # typed honest stop (unverified source / dead-run / off-target)
                if self.graph.has_suspect_self_loop(v):
                    suspect_waypoint = True  # walked through a known-ambiguous node — cap downstream
                continue  # re-observe; the arrival gate above judges where the walk left us
            # pre_actions (e.g. fill username + password) run BEFORE the edge's submit tap.
            for pre in edge.get("pre_actions", []):
                res = self._execute(pre)
                if not res.ok:
                    return self._action_outcome(res, step, v, actual)
            act_obj = edge["action"]
            src_scr = self.graph.screen(u)
            # L6: reveal-scrolling widens action reach, so it requires the source be
            # EXACT-verified (this observation reproduces u's id) — NOT mere `corroborated`,
            # which namespace-trusted recovery sets (a bare "we're in this activity" landing
            # on an unrecorded same-namespace interstitial must not let the rung scroll it).
            exact_src = (src_scr is not None and
                         observed_matches_id(ns, xml, src_scr, focus, config_for(src_scr)))
            res = None
            if act_obj.in_region and act_obj.action_type in ("click", "long_click")                     and exact_src:
                # IN-REGION PRE-ROUTE (S23 decoy finding): the recorded element lived INSIDE
                # the adapter region, so it must resolve REGION-BOUND through the reveal
                # machinery (in-container match, L5 check+act from one settled dump,
                # scrolling as needed) — NEVER via a global xpath, which can bind a chrome
                # decoy carrying the same text (Samsung's rotating search-plate suggestions
                # cycle through setting names; a global resolve confidently taps the search
                # box while the real row sits below the fold).
                report = _reveal.attempt_reveal(
                    self.driver, act_obj, src_scr, self._observe,
                    clock=self.clock, sleep=self.sleep)
                if report.reason == _reveal.REVEALED:
                    res = actions.ActionResult(True) if report.acted else self._execute(act_obj)
                elif report.reason != _reveal.NOT_ELIGIBLE:
                    out = self._action_outcome(
                        actions.ActionResult(False, actions.NOT_RESOLVED,
                                             "in-region element did not resolve"),
                        step, v, actual)
                    out.detail = f"{report.reason} after {report.steps} reveal step(s)"
                    out.reveal = report
                    return out
                # NOT_ELIGIBLE (no bounds / no live region) -> the global path below
            if res is None:
                res = self._execute(act_obj)
            if not res.ok and res.reason == actions.NOT_RESOLVED:
                # §3 reveal rung — same trigger as the engine (selector unreachable on the
                # source screen), gated by L6 (exact_src above).
                if not exact_src:
                    return self._action_outcome(res, step, v, actual)
                report = _reveal.attempt_reveal(
                    self.driver, act_obj, src_scr, self._observe,
                    clock=self.clock, sleep=self.sleep)
                if report.reason == _reveal.REVEALED:
                    # a tap-class action ran inline (L5); a non-tap action is now on screen ->
                    # run the recorded action against its real selector (never a bare tap).
                    res = actions.ActionResult(True) if report.acted else self._execute(act_obj)
                elif report.reason != _reveal.NOT_ELIGIBLE:
                    out = self._action_outcome(res, step, v, actual)
                    out.detail = f"{report.reason} after {report.steps} reveal step(s)"
                    out.reveal = report
                    return out
            if not res.ok:
                # credential/coordinate refusal, or a drifted selector/action (content_drift)
                return self._action_outcome(res, step, v, actual)
            corroborated = True  # a recorded edge was walked from a recognized screen

        return NavOutcome("force_failed", expected_id=to_id, detail="step cap reached")


def navigate(graph: Graph, from_id: str, to_id: str, driver, **kwargs) -> NavOutcome:
    return Navigator(graph, driver, **kwargs).navigate(from_id, to_id)
