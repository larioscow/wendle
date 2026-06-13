"""The GRAPH-BUILD CORE — one builder, two front-ends (v2 preparation).

`GraphBuilder` owns the graph and every piece of graph-touching in-flight state, and is the
ONLY path that mints nodes (via `resolve_identity`) and commits transitions. Two front-ends
drive it:

  * the human-gesture recorder (`RecordSession`): decodes getevent gestures into Actions,
    binds them to the tap-time frame, then delegates here;
  * a future external-crawl ingester (v2): an autonomous explorer actuates the device itself
    and reports the Action it took (+ optionally the element bounds), then calls the SAME
    `enter()` / `commit_transition()`.

Because both routes go through one minter and one commit, a crawl-built graph is
byte-identical to a hand-recorded one — keeping record→replay the foundation (invariant #2).

Honesty gates the builder enforces REGARDLESS of front-end:
  * an unsettled observation can never mint a confident node (`resolve_identity` forces
    volatile "V"+structure_id, low confidence — structural, not opt-in);
  * `source_volatile` is recomputed from the graph, never trusted from the caller;
  * the §2.8 suspect tripwire runs on raw before/after dumps (coordinate-free);
  * `stage_pending` rejects a sensitive Action carrying a literal (redaction at ingest).

Coordinate-vs-selector degrade: the §2.7/§2.8 swipe rungs need the gesture's pixel context
(`BindContext`). The human path always has it; a selector-only crawler that cannot supply
geometry gets the HONEST degrade — each coordinate rung refuses at its top (probe / not
continuous), never guesses. The suspect tripwire needs no coordinates and runs unchanged.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from wendle.capture.affordance import in_nav_container
from wendle.capture.hierarchy import parse_hierarchy
from wendle.capture.text_entry import is_editable, is_ime_node
from wendle.capture.types import Snapshot
from wendle.fingerprint.compose import COMPOSE_PROFILE, resolve_profile
from wendle.fingerprint.signature import (
    has_collapsing_list,
    is_launcher_namespace,
    outside_region_value_bearing,
    region_geometry,
    structure_id,
)
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Transition
from wendle.record.identity import resolve_identity

_WEIGHT = {"high": 1.0, "medium": 1.5, "coordinate_only": 10.0}

# Static per-action-class routing prior (replaces the old swipe weight PENALTY).
# No-op swipes are now dropped by the effectiveness filter; a swipe that DID change
# state is a real edge but still less reliable to replay than a tap, so it costs a
# little more — far less than the old +1.5 penalty.
# 'scroll' = a chrome-fork continuation edge (a reveal-classified hop whose ids forked; the
# navigator walks it via reveal.walk_to_node, replay skips it). Pinned swipe-like and CHEAP
# (0.0 prior over base 1.0) so the router prefers the one observed scroll hop over a detour.
_CLASS_PRIOR = {"swipe": 0.5, "system_key": 0.0, "navigate": 0.0, "scroll": 0.0}


def weight_from(replayability: str) -> float:
    return _WEIGHT.get(replayability, 1.0)


def _action_class(action) -> str:
    if action.action_type == "swipe":
        return "swipe"
    if action.action_type == "keyevent":
        return "system_key"
    return "navigate"


def _profile_name(cfg, namespace: str) -> str:
    if is_launcher_namespace(namespace):
        return "launcher"
    return "compose" if cfg is COMPOSE_PROFILE else "view"


def _screen_type(namespace: str, package: Optional[str]) -> str:
    if is_launcher_namespace(namespace):
        return "homescreen"
    if package == "com.android.systemui":
        return "systemui"
    if package and ".settings" in package:
        return "settings"
    return "app"


def has_interactive(nodes) -> bool:
    """True if the screen presents an app-owned, user-ACTIONABLE affordance — clickable,
    checkable, focused, or an editable field. System UI is already stripped at parse
    (RULE 1); the IME is excluded here. A pure splash/loading screen (logo + spinner +
    a 'Loading…' label) has none, so it is treated as part of the LAUNCH, not the first
    real screen. (View-toolkit signal; a pure-Compose screen that exposes no clickable/
    focusable node is a known gap — it would defer the anchor until one appears.)"""
    for n in nodes:
        if is_ime_node(n):
            continue
        if n.clickable or n.checkable or n.focused or is_editable(n):
            return True
    return False


@dataclass
class EnterResult:
    """One observed-and-minted screen arrival: the gate-resolved id, the parsed snapshot,
    whether the observation settled, and the identity decision (whose `node_remap` the
    next commit reconciles)."""

    id: str
    snapshot: Snapshot
    settled: bool
    decision: object  # IdentityDecision


@dataclass
class BindContext:
    """The gesture's pixel context for the §2.7/§2.8 coordinate rungs.

    The human recorder fills it fully (tap point, swipe end, bound-element bounds). An
    external crawler that reports only a Selector leaves px/py None — every coordinate
    rung then refuses honestly (probe / no continuity) instead of guessing; supplying the
    actuated element's bounds (cheap via uiautomator2 `.info`) restores full classification
    and byte-identical graphs."""

    px: Optional[int] = None
    py: Optional[int] = None
    end: Optional[Tuple[int, int]] = None
    bounds: Optional[Tuple[int, int, int, int]] = None
    landed: bool = True


class GraphBuilder:
    """Owns the graph + graph-touching in-flight state; both front-ends delegate here."""

    def __init__(self, sink: Optional[Callable[[dict], None]] = None,
                 lock: Optional[threading.Lock] = None,
                 on_rename: Optional[Callable[[dict], None]] = None):
        self.graph = Graph()
        self.sink = sink or (lambda env: None)
        self.lock = lock or threading.Lock()
        # invoked (outside the lock) with each non-empty node_remap so a front-end can
        # repair ITS OWN id-bearing state (the recorder's in-flight _typing_screen)
        self._on_rename = on_rename
        self.current_id: Optional[str] = None
        self.current_snapshot: Optional[Snapshot] = None
        self.provisional: list[str] = []
        self._pending: list[tuple[str, str, Action]] = []  # [(screen_id, namespace, pre_action)]
        # RULE 2: while True, we're still launching (past the launcher, on a splash/loading
        # screen with no real affordance) — the am_start anchor is DEFERRED to the first
        # interactive app screen so the flow begins there and replay skips launch frames.
        self._launching: bool = False
        # Raw settled dumps for §2.7/§2.8 gesture classification (lazy-region design):
        # _last_enter_xml = the dump the most recent enter() fingerprinted; _settled_xml =
        # the CURRENT screen's arrival dump (the "before" frame of the next gesture).
        self._last_enter_xml: Optional[str] = None
        self._settled_xml: Optional[str] = None

    # ---- screen entry (the pure half: identity + mint + upsert) ----

    def enter(self, xml: str, ns: str, settled: bool, focus: Optional[str]) -> EnterResult:
        """Mint/resolve the observed screen — the observe-after primitive BOTH front-ends
        call. The device half (settle loop, dumps) belongs to the caller; this half is a
        pure function of the observation plus the graph."""
        # The coarse structure tier is computed for EVERY screen (settled or volatile):
        # it is the only id a dynamic feed reproduces on replay (STRUCTURE verify tier).
        struct_id = structure_id(ns, xml, focus_pkg=focus)
        cfg = resolve_profile(xml, ns)  # the gate fingerprints with this; also the settled profile
        pname = _profile_name(cfg, ns) if settled else "volatile"  # never-settle -> volatile
        # ONE identity gate (task #17b): settled identity routes through resolve_identity, which
        # refines structure-twins apart on an OBSERVED collision (and returns the rename to fix).
        # Volatile -> "V"+structure_id, launcher -> the home id: today's behavior, in the gate.
        # enter() is the SOLE node MINTER; the gate only mutates EXISTING nodes and carries the
        # chrome_digest/coarse_id this minted node must hold.
        dec = resolve_identity(self.graph, ns, xml, focus, settled, cfg)
        sid = dec.id
        pkg, _, activity = ns.partition("/")
        screen = Screen(
            id=sid,
            namespace=ns,
            structure_id=struct_id,
            screen_type=_screen_type(ns, pkg or None),
            package=pkg or None,
            activity=activity or None,
            profile_name=pname,
            fingerprint_confidence="high" if settled else "low",
            volatile=not settled,
            chrome_digest=dec.chrome_digest,  # carriers: enables next-visit collision detection
            coarse_id=dec.coarse_id,          # non-None -> this is a refined twin
            # record adapter-dominance as an IDENTITY-CLASS property so the navigator's HW2 gate
            # keys on it, not the live row count. Computed ROW-COUNT-INDEPENDENTLY (list PRESENCE,
            # not the leaf-ratio) so a sparse/empty first capture can't mislabel a genuine list
            # page False and let a refined twin be claimed confident-wrong (re-verification CRITICAL).
            adapter_dominant=settled and has_collapsing_list(xml, focus_pkg=focus),
            # L3: did any outside-region VALUE survive into this id's hash? Recorded from the
            # SAME settled dump under the SAME resolved profile the fingerprint used (§2.5);
            # unsettled (volatile) screens are never value-bearing.
            value_bearing=settled and outside_region_value_bearing(xml, cfg, focus_pkg=focus),
        )
        # launcher home is always a keyevent anchor; per-app launch anchors are set
        # in commit_transition (an app entered FROM home is its launch activity).
        if is_launcher_namespace(ns):
            screen.force_action = ForceAction("keyevent", "3", verified_fp=sid)

        self.graph.upsert_screen(screen)
        self.graph._profiles[ns] = pname
        snap = Snapshot(t_start=0.0, t_end=0.0, hierarchy_hash=sid,
                        nodes=parse_hierarchy(xml, focus_pkg=focus))
        self._last_enter_xml = xml  # §2.7/§2.8: the raw dump behind this arrival
        return EnterResult(id=sid, snapshot=snap, settled=settled, decision=dec)

    def begin(self, entered: EnterResult) -> Screen:
        """Anchor the session on its first observed screen."""
        self.current_id, self.current_snapshot = entered.id, entered.snapshot
        self._settled_xml = self._last_enter_xml
        return self.graph.screen(entered.id)

    # ---- the commit core: (1) launch anchor, (2a) checkable, (2b) effectiveness, (3) edge ----

    def commit_transition(self, *, action: Action, after: EnterResult,
                          bind: Optional[BindContext] = None,
                          checked: Optional[Action] = None,
                          needs_confirmation: bool = False) -> Optional[Transition]:
        """Commit one actuated Action against the freshly-entered `after` screen.

        The caller has ALREADY actuated the action (human finger or crawler) and observed
        the result via `enter()`. This core classifies and records it — launch-anchor
        stamping, same-screen set_checked staging, the §2.7/§2.8 effectiveness/reveal/
        suspect rules, edge build + dedup — identically for every front-end."""
        bind = bind or BindContext()
        if self.current_id is None:
            # ingest-seam misuse (commit before begin()/enter()): refuse TYPED before any
            # write — branch (3) would otherwise mint a literal-None source node and crash.
            raise ValueError("commit_transition requires a current screen — call begin() "
                             "with an EnterResult first")
        px, py, landed = bind.px, bind.py, bind.landed
        # the §7 bind-frame fields, when the front-end supplied geometry and the action
        # wasn't already stamped (the human pre-amble stamps these itself)
        if action.bounds is None and bind.bounds is not None:
            action.bounds = bind.bounds
            action.in_region = self._point_in_region(
                (bind.bounds[0] + bind.bounds[2]) // 2,
                (bind.bounds[1] + bind.bounds[3]) // 2, bounds=bind.bounds)
        source = self.current_id
        tgt_id, tgt_snap, tgt_settled, dec = after.id, after.snapshot, after.settled, after.decision
        # SPLIT/COARSEN-WHILE-SOURCE (task #17b): settling the TARGET may have refined the
        # SOURCE's twin family — a SPLIT rekeys F -> T_old, or runaway churn COARSENS the whole
        # family back into F. `source`, current_id, the pending typing tags, and the provisional
        # 'u->v#k' strings all still hold a vanished id — repair them in this one pass, BEFORE
        # any deref of `source` or take_pending below, so no edge references a dead node and no
        # typed value is dropped.
        if dec.node_remap:
            if source in dec.node_remap:
                source = dec.node_remap[source]
            self.apply_identity_rename(dec)
        src_screen = self.graph.screen(source)
        tgt_screen = self.graph.screen(tgt_id)
        src_ns = src_screen.namespace if src_screen is not None else (source or "")
        # honesty gate: volatility of the source is a fact about the GRAPH, recomputed here —
        # a front-end can never mint a confident edge out of a volatile node by mislabeling it
        source_volatile = bool(src_screen.volatile) if src_screen is not None else False
        # Returning to the LAUNCHER abandons any in-flight (deferred-splash) launch. Without
        # this reset a sticky `_launching` mis-stamps the NEXT app's anchor self_routing, and
        # the launch ladder would skip its recorded component for no reason.
        if tgt_screen is not None and (tgt_screen.screen_type == "homescreen"
                                       or is_launcher_namespace(tgt_screen.namespace)):
            self._launching = False

        # (1) Launch anchor (RULE 2). An app entered FROM the launcher is reached by COLD-
        # LAUNCHING it (am_start), not by replaying a fragile home-icon tap. The anchor goes on
        # the first app-owned INTERACTIVE screen — NOT a transient splash/loading screen that
        # only shows a logo/spinner/"Cargando…" (no affordance; it auto-advances). When the
        # first screen out of the launcher is a non-interactive splash, the anchor is DEFERRED
        # (`_launching`) to the first interactive app screen; the recorded flow then begins
        # there and replay skips the pre-anchor launch frames. NOTHING is dropped from the
        # recording — only the anchor placement (and thus the replay start) moves.
        leaving_launcher = src_screen is not None and src_screen.screen_type == "homescreen"
        is_app_screen = (
            tgt_screen is not None
            and not is_launcher_namespace(tgt_screen.namespace)
            and bool(tgt_screen.package)
            and bool(tgt_screen.activity)
        )
        if (leaving_launcher or self._launching) and is_app_screen:
            if has_interactive(tgt_snap.nodes):
                if tgt_screen.force_action is None:
                    # anchor even a VOLATILE app-home (a dynamic feed never settles, but it IS
                    # interactive, so it's the first real screen — am_start launches it; the
                    # anchor verifies by namespace). Stamp PROVENANCE: if we got here by DEFERRING
                    # past a splash (`self._launching`), the recorded activity is a deep, likely
                    # non-exported surface (`am start -n` doomed) -> self_routing, so the launch
                    # ladder skips the recorded component and lets the package default route in.
                    provenance = "self_routing" if self._launching else "launcher_entry"
                    tgt_screen.force_action = ForceAction(
                        "am_start", f"{tgt_screen.package}/{tgt_screen.activity}", verified_fp=tgt_id,
                        provenance=provenance,
                    )
                self._launching = False
            else:
                # a non-interactive splash/loading screen: still launching, defer the anchor
                self._launching = True
                self.sink({"v": 1, "event_type": "launch_frame", "screen": tgt_id,
                           "namespace": tgt_screen.namespace})

        # (2a) A checkable flip is ALWAYS a same-screen state set, regardless of source==tgt_id.
        # detect_checkable_entry only returns non-None when it RE-FINDS the tapped widget (by
        # resource-id) flipped in the AFTER snapshot — which proves the tap did NOT navigate
        # away from it. On a volatile form the fingerprint id can jitter (settled "a.." ->
        # volatile "Va.." or to a reflowed "Vb..") even though you stayed on the same screen
        # and a SEPARATE button does the advancing; that id change is reflow, not navigation,
        # so it must not turn the checkbox tap into an edge. The flip rides the next submit
        # edge as an idempotent set_checked pre_action. (If the tap HAD truly navigated, the
        # box would be ABSENT from `after` -> checked is None -> it falls through to a normal
        # navigating edge below, so a checkbox that genuinely changes screens is unaffected.)
        either_volatile = source_volatile or (tgt_screen.volatile if tgt_screen else False)
        if checked is not None and src_screen is not None:
            if self.stage_pending(checked, source, src_ns):
                self.sink({"v": 1, "event_type": "set_checked", "screen": source,
                           "field": (checked.value or {}).get("param") or checked.selector.value,
                           "checked": checked.value["checked"], "sensitive": checked.sensitive})
            self.current_snapshot = tgt_snap
            self._settled_xml = self._last_enter_xml
            return None

        # (2b) Effectiveness filter (DroidBot: an action that didn't change state is no edge).
        # A non-checkable same-screen tap is an honest probe — settled-only, since a "V"+ns id
        # is not a reliable state (a volatile self-loop is kept and later excluded by routing).
        # Lazy-region rules (design §2.7/§2.8):
        #   * a SWIPE is replayable `reveal` ONLY when region-bound and content-advance —
        #     comparison at the COARSE tier; a reveal scroll whose ids FORKED is recorded as a
        #     `scroll`-CLASS continuation edge (Cap 1: the navigator walks it, replay skips it),
        #     NEVER a navigate edge; retreat sense (the pull-to-refresh shape) and pans on
        #     non-region scrollables (maps, canvases) are `probe` and mint no edge;
        #   * a same-id TAP across MATERIALLY DIFFERENT region content is an invisible
        #     navigation after collapse — recorded as a SUSPECT edge, never swallowed.
        suspect = False
        same_coarse = (
            src_screen is not None and tgt_screen is not None
            and (src_screen.coarse_id or source) == (tgt_screen.coarse_id or tgt_id))
        same_ns = (src_screen is not None and tgt_screen is not None
                   and src_screen.namespace == tgt_screen.namespace)
        # Chrome-fork tolerance (S23-confirmed: OEM collapsing toolbars fork BOTH identity
        # tiers on scroll, by default): a content-advance gesture along a detected region's
        # stack axis whose target dump still shows that region (substantial rebind overlap,
        # same namespace) is a SCROLL of the same logical screen even when the ids forked.
        # Misclassification direction: a real swipe-navigation recorded as intra drops a hop
        # (an honest gap the reconciler materializes) — never a wrong edge.
        swipe_same_screen = action.action_type == "swipe" and (
            same_coarse or (same_ns and self._swipe_region_continuity(px, py, action.end)))
        if src_screen is not None and not either_volatile and (
                swipe_same_screen if action.action_type == "swipe" else source == tgt_id):
            if action.action_type != "swipe" and self._region_content_differs():
                suspect = True  # §2.8 tripwire: fall through to (3); the edge records flagged
            else:
                if action.action_type == "swipe":
                    action.intent = ("reveal"
                                     if self._is_region_advance_swipe(action, px, py)
                                     else "probe")
                else:
                    action.intent = "probe"
                src_screen.intra_actions.append(action)
                scroll_edge = None
                if tgt_id != source:
                    # CHROME-FORK CONTINUATION (Cap 1): the scroll moved us to a forked twin —
                    # record the relationship as a `scroll`-class edge ONLY when the gesture is
                    # a reveal (content-advance). A probe/retreat hop mints NO edge, so the
                    # navigator's reveal-walk can never replay a pull-to-refresh (§3.4). The
                    # edge carries NO pre_actions (never drains a staged credential onto an edge
                    # replay skips) and is NOT a routed Screen.action; the gesture stays intra
                    # evidence above. Dedup is per-(source,target) for the scroll class.
                    if action.intent == "reveal":
                        scroll_edge = self._commit_scroll_edge(source, tgt_id, action)
                    self.current_id = tgt_id  # same coarse screen, refined chrome drift
                self.current_snapshot = tgt_snap  # same screen; refresh the bound snapshot
                self._settled_xml = self._last_enter_xml
                return scroll_edge

        # (3) Genuine inter-screen edge (or a volatile same-screen self-loop). `checked` is
        # always None here — a flip was handled as a same-screen set_checked in (2a) above.
        action.intent = "navigate"
        action_class = _action_class(action)
        # Text typed / boxes checked on THIS screen ride the submit edge so they replay BEFORE
        # the tap (set-state-then-submit) — see Transition.pre_actions.
        pre = self.take_pending(source)
        # A SWIPE binds to coordinates BY NATURE, so its coordinate_only replayability is NOT a
        # fragility signal (unlike a coordinate-only TAP, which means selector synthesis failed).
        # Route it at its class prior over a stable base, not the 10x coordinate penalty.
        base = 1.0 if action_class == "swipe" else weight_from(action.replayability)
        edge_provisional = needs_confirmation or source_volatile or suspect
        # GLOBAL-NAV affordance: a tap on a stable-selector member of a detected nav container
        # (tab bar / bottom nav / drawer) — routable from any in-app screen by VERIFY-BY-
        # AFFORDANCE. NEVER on a provisional/suspect edge (those are known-ambiguous). Pure over
        # the settled source dump + the action's bounds (both ingest-supplied -> convergence-safe).
        global_aff = (
            not edge_provisional
            and action.action_type == "click"
            and action.selector.kind in ("content_desc", "resource_id", "label", "text")
            and action.bounds is not None
            and self._settled_xml is not None
            and in_nav_container(self._settled_xml, action.bounds))
        t = Transition(
            source=source,
            target=tgt_id,
            action=action,
            weight=base + _CLASS_PRIOR.get(action_class, 0.0),
            action_class=action_class,
            pre_actions=pre,
            # a tap out of a volatile screen can't be trusted -> always provisional;
            # a §2.8 suspect self-loop is low-confidence by definition
            needs_confirmation=edge_provisional,
            settled=tgt_settled,
            landed_on_real_element=landed,
            suspect_self_loop=suspect,
            global_affordance=global_aff,
        )
        if suspect:
            # §2.8: the only seam where identical-chrome region-content twins are observable
            # at all — warn at record time (value-free) and flag the edge; navigation over it
            # reports arrived_unverified, never a confident arrival.
            self.sink({"v": 1, "event_type": "suspect_self_loop", "screen": source,
                       "action_type": action.action_type, "selector_kind": action.selector.kind})
        # Revisiting a screen and repeating the same action is common — don't grow a
        # duplicate parallel edge. BUT a submit that carries set_text (pre) is NOT the same
        # as the bare tap — always add it, else the dedup would silently drop the credential.
        if pre or not self._duplicate_edge(source, tgt_id, action):
            edge_id = self.graph.add_transition(t)
            if t.needs_confirmation:
                self.provisional.append(edge_id)
            self.sink(
                {
                    "v": 1,
                    "event_type": "transition",
                    "source": source,
                    "target": tgt_id,
                    "action_type": action.action_type,
                    "action_class": action_class,
                    "needs_confirmation": t.needs_confirmation,
                    "settled": tgt_settled,
                }
            )
        if src_screen is not None:  # a dead/unknown source id must not crash post-commit
            seen = {(a.selector.kind, a.selector.value) for a in src_screen.actions}
            if (action.selector.kind, action.selector.value) not in seen:
                src_screen.actions.append(action)
        self.current_id, self.current_snapshot = tgt_id, tgt_snap
        self._settled_xml = self._last_enter_xml
        return t

    # ---- pending (set_text / set_checked) staging + drain — shared by both front-ends ----

    def stage_pending(self, action: Action, screen_id: str, namespace: str) -> bool:
        """Stage a state-setting pre_action to ride the next submit edge. REDACTION GATE
        (invariant #4): a sensitive action may carry only a {param} handle (plus the
        checked/replay_mode flags) — a literal typed value is rejected with an honest
        marker, never stored. Lock-free append (callers keep today's locking discipline:
        the typing FSM appends under the session lock, the checkable path appends bare)."""
        if action.sensitive:
            value_keys = set(action.value or {})
            if "text" in value_keys or not value_keys <= {"param", "checked", "replay_mode"}:
                self.sink({"v": 1, "event_type": "unreplayable_field",
                           "screen": screen_id, "sensitive": True})
                return False
        self._pending.append((screen_id, namespace, action))
        return True

    def take_pending(self, source: Optional[str]) -> List[Action]:
        """Remove and return the pre_actions (set_text/set_checked) staged for `source`, in
        order. A VOLATILE submit screen also drains entries sharing its NAMESPACE: a volatile
        form's "V"+structure_id jitters as rows validate (and the keyboard-up typing fingerprint
        differs from the keyboard-down submit), so the stable namespace bridges them. A SETTLED
        source matches by id ONLY, so two distinct settled forms of one single-Activity app don't
        steal each other's pending — the field-tag picks current_id when already on the app, so
        that exact-id match holds (see RecordSession._track_text)."""
        scr = self.graph.screen(source) if source else None
        ns = scr.namespace if scr else None
        vol = bool(scr.volatile) if scr else False

        def _mine(sid, n):
            return sid == source or (vol and ns is not None and n == ns)

        with self.lock:
            mine = [a for (sid, n, a) in self._pending if _mine(sid, n)]
            self._pending = [(sid, n, a) for (sid, n, a) in self._pending if not _mine(sid, n)]
        return mine

    def drop_stale_pending(self, keep: Optional[str]) -> None:
        """Drop pending NOT staged for `keep` (we navigated away without a recorded submit
        edge — e.g. the keyboard's own 'Go') and surface an honest marker. Mirror take_pending's
        bridge: a VOLATILE `keep` screen also KEEPS entries sharing its namespace — a field typed
        a cycle earlier on the same volatile app carries a different fingerprint but is NOT stale,
        so it must survive to ride that app's submit edge (the launch-then-type case)."""
        keep_scr = self.graph.screen(keep) if keep else None
        keep_ns = keep_scr.namespace if keep_scr else None
        keep_vol = bool(keep_scr.volatile) if keep_scr else False

        def _survives(sid, n):
            return sid == keep or (keep_vol and keep_ns is not None and n == keep_ns)

        with self.lock:
            stale = [(sid, n, a) for (sid, n, a) in self._pending if not _survives(sid, n)]
            self._pending = [(sid, n, a) for (sid, n, a) in self._pending if _survives(sid, n)]
        for sid, _n, a in stale:
            ev = "uncommitted_state" if a.action_type == "set_checked" else "uncommitted_text"
            self.sink({"v": 1, "event_type": ev, "screen": sid, "sensitive": a.sensitive})

    # ---- identity-rekey repair (task #17b): a split/coarsen renames nodes old -> new ----

    def remap_holders(self, node_remap: dict, edge_remap: Optional[dict] = None) -> None:
        """Repair EVERY holder of a vanished node id after a rename — current_id, the pending
        typing tags, the provisional edge-id strings — and invoke the front-end's `on_rename`
        callback so it can repair its OWN id-bearing state (the recorder's in-flight
        _typing_screen). The pending remap and the callback run under ONE lock hold (the
        callback contract: `on_rename` is invoked WITH the builder lock held and must not
        re-acquire it) — releasing between them opens a window where the refresher thread
        finalizes a field against the stale id and the staged credential is orphaned forever
        (Invariant #4; the pre-extraction _remap_id was atomic for exactly this reason).
        Shared by the gate's rename path (apply_identity_rename) and the human-merge coarsen
        path (mark_same)."""
        if not node_remap:
            return
        if self.current_id in node_remap:
            self.current_id = node_remap[self.current_id]
        with self.lock:
            self._pending = [(node_remap.get(sid, sid), n, a)
                             for (sid, n, a) in self._pending]
            if self._on_rename is not None:
                self._on_rename(node_remap)
        self._remap_provisional(edge_remap)

    def apply_identity_rename(self, dec) -> None:
        """Reconcile builder state after a gate decision renamed EXISTING nodes — a SPLIT
        (F -> T_old) or a COARSEN (every family member -> F) — then emit the rekey events."""
        nmap = dec.node_remap
        if not nmap:
            return
        self.remap_holders(nmap, dec.remap)
        for old, new in nmap.items():
            tgt = self.graph.screen(new)
            self.sink({"v": 1, "event_type": "rekey", "from": old, "to": new,
                       "namespace": tgt.namespace if tgt else None})

    def _remap_provisional(self, remap: Optional[dict]) -> None:
        """Rebuild the provisional 'u->v#k' edge-id strings from the rekey's edge-key remap. The
        integer key changes too (rekey re-adds edges with fresh keys), so a textual node-substitution
        would be WRONG — reject_edge/correct_edge parse these strings and need the real edge key."""
        if not remap:
            return

        def parse(s):
            u, rest = s.split("->")
            v, k = rest.split("#")
            return (u, v, int(k))

        def fmt(t):
            return f"{t[0]}->{t[1]}#{t[2]}"

        self.provisional = [fmt(remap[parse(s)]) if parse(s) in remap else s
                            for s in self.provisional]

    # ---- lazy-region gesture classification (design §2.7 / §2.8) ----
    # Every coordinate rung refuses at its top when the front-end supplied no geometry
    # (px is None) — the honest, eligibility-denying degrade for a selector-only crawler.

    def _swipe_region_continuity(self, px: Optional[int], py: Optional[int], end) -> bool:
        """§2.7 chrome-fork rule: is this gesture a scroll of the SAME region across two
        dumps whose screen ids forked? Requires: start inside a detected region of the
        before-dump; content-advance ALONG that region's stack axis (excludes row-level
        horizontal swipes and retreat/pull-to-refresh); the after-dump still shows the
        region (>=50%-of-smaller rebind overlap — the same continuity mechanic the reveal
        rung uses). Pagers cannot false-positive (page-sized children never form regions)."""
        before, after = self._settled_xml, self._last_enter_xml
        if px is None or py is None or before is None or after is None or end is None:
            return False
        region = None
        for r in region_geometry(before):
            left, top, right, bottom = r["bounds"]
            if left <= px <= right and top <= py <= bottom:
                region = r
                break
        if region is None:
            return False
        from wendle.reveal import _overlap_area, _rebind, is_content_advance
        if not is_content_advance(region["axis"], px, py, end):
            return False
        rebound = _rebind(region_geometry(after), region["bounds"])
        if rebound is None:
            return False

        def area(b):
            return max(0, b[2] - b[0]) * max(0, b[3] - b[1])

        overlap = _overlap_area(rebound["bounds"], region["bounds"])
        return overlap >= 0.5 * min(area(rebound["bounds"]), area(region["bounds"]))

    def _point_in_region(self, x: int, y: int, bounds=None) -> bool:
        """Is the tapped node part of a detected adapter region of the current settled dump?
        With `bounds` (the tapped node's bounds) the test is DOM-AWARE — the node must be a
        DESCENDANT of a region container, not merely geometrically inside its bounds (a
        floating tab bar / FAB whose pixels overlap a list is NOT in_region; it is global
        chrome that replays as a normal tap, never reveal-scrolled). Without bounds it falls
        back to the geometric test (legacy callers)."""
        if self._settled_xml is None:
            return False
        if bounds is not None:
            from wendle.reveal import node_in_region_subtree
            return node_in_region_subtree(self._settled_xml, bounds)
        return any(
            r["bounds"][0] <= x <= r["bounds"][2] and r["bounds"][1] <= y <= r["bounds"][3]
            for r in region_geometry(self._settled_xml))

    def _is_region_advance_swipe(self, action, px: Optional[int], py: Optional[int]) -> bool:
        """§2.7: a swipe is replayable `reveal` ONLY when its start point lies inside a
        detected adapter region of the CURRENT screen's settled dump AND its sense is
        CONTENT-ADVANCE for that region's stack axis (finger toward the axis start brings
        further content into view). No dump / no region / no geometry / retreat sense ->
        probe (the honest, eligibility-denying direction; pull-to-refresh and pans never
        become replayable)."""
        from wendle.reveal import is_content_advance
        end = getattr(action, "end", None)
        if px is None or py is None or self._settled_xml is None or end is None:
            return False
        for region in region_geometry(self._settled_xml):
            left, top, right, bottom = region["bounds"]
            if left <= px <= right and top <= py <= bottom:
                # axis-dominant content-advance ONLY — a near-horizontal row pan (archive /
                # dismiss) on a vertical list is NOT a reveal; replaying it would swipe-mutate
                # whatever row now sits at those pixels.
                return is_content_advance(region["axis"], px, py, end)
        return False

    def _region_content_differs(self) -> bool:
        """§2.8: do the before/after dumps' detected regions differ in the MAJORITY of their
        per-child value digests? Conservative: regions must correspond 1:1 (same count) and
        only positionally-compared children count — geometry changes are not the tripwire's
        case. True = a same-id tap crossed materially different region content (an invisible
        navigation after collapse) -> record a SUSPECT edge, never swallow it as a probe.
        Coordinate-free: runs identically for a selector-only crawler."""
        before, after = self._settled_xml, self._last_enter_xml
        if before is None or after is None or before == after:
            return False
        regions_before = region_geometry(before)
        regions_after = region_geometry(after)
        if not regions_before or len(regions_before) != len(regions_after):
            return False
        for rb, ra in zip(regions_before, regions_after):
            db, da = rb["digests"], ra["digests"]
            n = min(len(db), len(da))
            if n and sum(1 for i in range(n) if db[i] != da[i]) * 2 > n:
                return True
        return False

    def _commit_scroll_edge(self, source: str, target: str, action):
        """Mint (at most one per source->target) the chrome-fork continuation edge. Weight is
        pinned swipe-like (base 1.0 + the cheap 'scroll' prior), NOT the coordinate_only 10x
        fall-through; needs_confirmation=False (the (2b) branch only runs under not-volatile,
        so both sides settled). A fresh Action copy is stored so later in-place edits to the
        intra-action's fields can't mutate the edge."""
        if self._has_scroll_edge(source, target):
            return None
        from dataclasses import replace as _replace
        edge_action = _replace(action)  # decouple from the intra_actions entry
        t = Transition(source=source, target=target, action=edge_action,
                       weight=1.0 + _CLASS_PRIOR["scroll"], action_class="scroll",
                       pre_actions=[], needs_confirmation=False, settled=True,
                       landed_on_real_element=True)
        self.graph.add_transition(t)
        self.sink({"v": 1, "event_type": "transition", "source": source, "target": target,
                   "action_type": action.action_type, "action_class": "scroll",
                   "needs_confirmation": False, "settled": True})
        return t

    def _has_scroll_edge(self, source: str, target: str) -> bool:
        if not self.graph.g.has_edge(source, target):
            return False
        return any(data.get("action_class") == "scroll"
                   for _k, data in self.graph.g[source][target].items())

    def _duplicate_edge(self, source: str, target: str, action) -> bool:
        # An edge is a duplicate only if it is the SAME action: same selector, type, swipe
        # END point, and value. Two swipes from one element that differ only in direction
        # (up-dismiss vs down-dismiss) share a selector but carry distinct `end`s and are
        # genuinely different replay alternatives — they must both survive (review-2 #B).
        if not self.graph.g.has_edge(source, target):
            return False
        for _k, data in self.graph.g[source][target].items():
            a = data.get("action")
            if (
                a is not None
                and a.selector.kind == action.selector.kind
                and a.selector.value == action.selector.value
                and a.action_type == action.action_type
                and a.end == action.end
                and a.value == action.value
            ):
                return True
        return False
