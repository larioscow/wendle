from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from wendle.calibration.scaling import scale_to_pixels
from wendle.capture.hierarchy import node_at, parse_hierarchy, plausible_bind_target
from wendle.capture.recorder import detect_action
from wendle.capture.text_entry import (
    detect_checkable_entry,
    detect_text_entry,
    field_identity,
    is_editable,
    is_ime_node,
    pick_ime_target,
)
from wendle.capture.types import Gesture, Snapshot
from wendle.fingerprint.compose import (
    COMPOSE_PROFILE,
    resolve_profile,
)
from wendle.fingerprint.dumpsys import focused_package, foreground_namespace
from wendle.fingerprint.signature import (
    fingerprint,
    has_collapsing_list,
    is_launcher_namespace,
    structure_id,
)
from wendle.models import Action, DeviceProfile, Screen
# The graph-build core lives in builder.py (the v2 ingestion seam — one minter, one commit
# path, shared by this human-gesture front-end and a future external-crawl ingester). The
# helpers are re-exported here for back-compat (tests and older callers import them from
# this module).
from wendle.record.builder import (  # noqa: F401 — re-exports are part of the API
    _CLASS_PRIOR,
    BindContext,
    GraphBuilder,
    _action_class,
    _profile_name,
    _screen_type,
    has_interactive,
    weight_from,
)
from wendle.record.identity import coarsen_family, lookup_identity
from wendle.record.observe import observe_settled


class RecordSession:
    """Manual-record loop: settle → fingerprint → detect tap → build the graph (§5).

    A gesture binds to the source screen's layout AT TAP TIME. The arrival (settle)
    snapshot is the fallback, but a screen can change AFTER we arrive yet BEFORE the tap
    (a collapsing toolbar, a late-loading row, the user scrolling) — binding to the stale
    arrival snapshot then maps the finger to the wrong element. So when `live_refresh` is
    on, a background thread keeps a fresh dump of the CURRENT screen, and record_gesture
    binds to that. `_enter`/`record_gesture` stay pure over the driver and are unit-tested
    with FakeDriver (the refresher is off by default so scripted-dump tests are stable).
    """

    def __init__(
        self,
        driver,
        profile: DeviceProfile,
        sink: Optional[Callable[[dict], None]] = None,
        dump_lock: Optional[threading.Lock] = None,
        settle_kwargs: Optional[dict] = None,
        live_refresh: bool = False,
        refresh_interval: float = 0.4,
        refresh_burst_interval: float = 0.02,
        refresh_burst_budget: int = 8,
    ):
        self.driver = driver
        self.profile = profile
        self.settle_kwargs = settle_kwargs or {}
        self.sink = sink or (lambda env: None)
        self.lock = dump_lock or threading.Lock()
        # The graph-build core (one minter, one commit path — the v2 ingestion seam). The
        # sink is passed as a late-bound indirection so reassigning `session.sink` after
        # construction (a test idiom) still reaches the builder's events.
        self._builder = GraphBuilder(sink=lambda env: self.sink(env), lock=self.lock,
                                     on_rename=self._on_node_remap)
        self.graph.device_profile = profile
        self.paused = False
        # Live tap-time binding (real-device only): (namespace, Snapshot) of the freshest
        # dump that was still on the current screen. record_gesture prefers it when its
        # namespace matches the source screen, so the tap binds to what was on screen now.
        self.live_refresh = live_refresh
        self.refresh_interval = refresh_interval
        # While a screen is still loading (churning fingerprint, or no affordance yet), the
        # refresher re-dumps back-to-back at this short interval instead of sleeping a full
        # cadence on a known-stale frame — uiautomator dumps are idle-gated, so a dump started
        # mid-load returns the SETTLED frame the instant the UI goes idle. Bounded by the
        # burst budget so a feed that never settles falls back to the normal cadence.
        self.refresh_burst_interval = refresh_burst_interval
        self.refresh_burst_budget = refresh_burst_budget
        self._fresh: Optional[dict] = None
        self._refresh_stop = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        # Text-entry FSM. The refresher tracks the focused field per cycle and, on a field
        # switch / blur, COMPUTES that field's set_text (detect_text_entry is pure) and
        # appends it to _pending tagged with the screen it was typed on. The graph is
        # mutated only on the main thread (record_gesture), which drains _pending whose tag
        # == the submit edge's source. All typing state is guarded by self.lock — both the
        # refresher and the main thread touch it. A login fills several fields, so _pending
        # is a LIST (one set_text per field), all riding on the submit edge's pre_actions.
        self._typing_before: Optional[Snapshot] = None   # field state at focus-entry
        self._typing_after: Optional[Snapshot] = None     # freshest focused dump
        self._typing_id: Optional[str] = None            # focused field identity
        self._typing_screen: Optional[str] = None         # screen id captured at focus-start
        self._typing_namespace: Optional[str] = None      # namespace of the field's OWN screen (#17)
        self._typing_reactive: bool = False               # app window churned -> per_key

    # ---- graph-state delegation (the builder is the owner; these keep every internal
    # call-site and test reference working verbatim — the strangler seam) ----

    @property
    def graph(self):
        return self._builder.graph

    @graph.setter
    def graph(self, value):
        self._builder.graph = value

    @property
    def current_id(self):
        return self._builder.current_id

    @current_id.setter
    def current_id(self, value):
        self._builder.current_id = value

    @property
    def current_snapshot(self):
        return self._builder.current_snapshot

    @current_snapshot.setter
    def current_snapshot(self, value):
        self._builder.current_snapshot = value

    @property
    def provisional(self):
        return self._builder.provisional

    @provisional.setter
    def provisional(self, value):
        self._builder.provisional = value

    @property
    def _pending(self):
        return self._builder._pending

    @_pending.setter
    def _pending(self, value):
        self._builder._pending = value

    @property
    def _launching(self):
        return self._builder._launching

    @_launching.setter
    def _launching(self, value):
        self._builder._launching = value

    @property
    def _settled_xml(self):
        return self._builder._settled_xml

    @_settled_xml.setter
    def _settled_xml(self, value):
        self._builder._settled_xml = value

    @property
    def _last_enter_xml(self):
        return self._builder._last_enter_xml

    @_last_enter_xml.setter
    def _last_enter_xml(self, value):
        self._builder._last_enter_xml = value

    # ---- screen entry: the DEVICE half (settle + dumps); the pure half lives in the
    # builder's enter() (the SOLE node minter — shared with the v2 ingestion seam) ----

    def _observe(self) -> tuple[str, str, bool, Optional[str]]:
        """Settle the live screen — the SHARED device half (record/observe.py), so the human
        recorder and the crawl ingester cannot drift in observation discipline."""
        return observe_settled(self.driver, self.lock, **self.settle_kwargs)

    def _enter(self):
        """Observe + mint via the shared builder (compat shim for resume()/tests)."""
        xml, ns, settled, focus = self._observe()
        entered = self._builder.enter(xml, ns, settled, focus)
        return entered.id, entered.snapshot, entered.settled, entered.decision

    def start(self) -> Screen:
        xml, ns, settled, focus = self._observe()
        screen = self._builder.begin(self._builder.enter(xml, ns, settled, focus))
        if self.live_refresh and self._refresh_thread is None:
            self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
            self._refresh_thread.start()
        return screen

    def stop(self) -> None:
        """Stop the background refresher (no-op if it was never started)."""
        self._refresh_stop.set()
        t = self._refresh_thread
        if t is not None:
            t.join(timeout=self.refresh_interval * 2 + 1.0)
            self._refresh_thread = None
        self._commit_in_flight()  # type-then-end-session (no following tap) still commits
        self._drop_stale_pending(keep=None)  # surface check-then-quit as uncommitted_state

    def _refresh_loop(self) -> None:
        """Keep `self._fresh` = an identified snapshot of the screen on display NOW (id,
        structure_id, profile, nodes), so (a) a tap binds to the current layout rather than
        the stale arrival snapshot, and (b) record_gesture can notice when the live screen
        no longer matches the node we think we're on (an unrecorded navigation). `stable`
        counts consecutive cycles with the same id — we only trust a screen as real once
        it has held for ≥1 extra cycle, so feed jitter doesn't mint phantom screens.
        Best-effort: any dump error just skips a cycle.

        CADENCE IS SETTLE-TRACKING, NOT FIXED (task #17a): while the frame is churning or
        shows no affordance (a page still loading), re-dump back-to-back — a fixed sleep here
        is exactly the stale window a tap can fall into (the on-device loading-overlay bind).
        Bounded by `refresh_burst_budget` consecutive churning cycles per screen; a new
        namespace replenishes it."""
        wait = self.refresh_interval
        streak = 0  # consecutive churning/affordance-less cycles since the last settle/new screen
        while not self._refresh_stop.wait(wait):
            if self.paused:
                wait = self.refresh_interval
                continue
            try:
                with self.lock:
                    xml = self.driver.dump_hierarchy()
                    act, win = self.driver.dumps()
                ns = foreground_namespace(act, win)
                focus = focused_package(win)
                cfg = resolve_profile(xml, ns)
                fp = fingerprint(ns, xml, cfg, focus_pkg=focus)
                struct = structure_id(ns, xml, focus_pkg=focus)
                snap = Snapshot(t_start=0.0, t_end=0.0, hierarchy_hash=fp,
                                nodes=parse_hierarchy(xml, focus_pkg=focus))
                prev = self._fresh
                stable = (prev["stable"] + 1) if (prev and prev.get("id") == fp) else 0
                new = {
                    "ns": ns, "snap": snap, "id": fp, "struct": struct,
                    "profile_name": _profile_name(cfg, ns), "focus": focus, "stable": stable,
                    # raw xml + cfg so reconcile can resolve identity through the gate (lookup
                    # only — a refresher dump never splits a twin family). Zero extra dumps.
                    "xml": xml, "cfg": cfg,
                }
                self._track_text(prev, new)
                self._fresh = new
                if stable >= 1 and self._has_interactive(snap.nodes):
                    streak = 0  # a real, settled, interactive screen — relax to cadence
                    wait = self.refresh_interval
                else:
                    # Count EVERY consecutive non-settled cycle, reset only on a relax above —
                    # keying the reset on a "new screen" let a namespace that flips every cycle
                    # reset the streak forever, so the budget never bound and the loop busy-
                    # waited at the burst rate (CPU pin). A genuine settle is the only replenish.
                    streak += 1
                    wait = self.refresh_burst_interval if streak <= self.refresh_burst_budget \
                        else self.refresh_interval  # bounded: never pin the CPU on a live feed
            except Exception:  # noqa: BLE001 — recorder must survive a flaky dump
                wait = self.refresh_interval
                continue

    # ---- text-entry FSM (all typing state guarded by self.lock) ----
    def _track_text(self, prev: Optional[dict], new: dict) -> None:
        """Track the editable IME target each cycle (focus-tolerant, so Compose fields count);
        on a field switch / blur, finalize the finished field into _pending (the graph is never
        touched here). Field identity falls back off resource_id (empty for Compose)."""
        cur = pick_ime_target(new["snap"].nodes)
        with self.lock:
            if cur is not None:
                if self._typing_before is None or field_identity(cur) != self._typing_id:
                    if self._typing_before is not None:
                        self._finalize_field()  # previous field done (multi-field login)
                    self._typing_before = self._capture_before(prev, cur, new["snap"])
                    self._typing_after = new["snap"]
                    self._typing_id = field_identity(cur)
                    # Tag the field to its OWN screen. When current_id is ALREADY on the field's app
                    # (same namespace) use it — it is the SETTLED form screen, the stable submit key
                    # even as the keyboard-up fingerprint jitters, and it keeps two settled forms of
                    # one app from stealing each other's typing. But when the main thread LAGS a
                    # just-launched app (current_id still the launcher) trust the refresher's FRESH
                    # detection — else the typing is stamped with the launcher and never drains onto
                    # the app's submit edge (the dropped-typing bug). The namespace is always the
                    # field's own — the key _take_pending / _drop_stale_pending bridge on. (#17)
                    cur_scr = self.graph.screen(self.current_id) if self.current_id else None
                    if cur_scr is not None and cur_scr.namespace == new["ns"]:
                        self._typing_screen = self.current_id
                    else:
                        self._typing_screen = new["id"]
                    self._typing_namespace = new["ns"]
                    self._typing_reactive = False
                else:
                    self._typing_after = new["snap"]  # same field — keep the freshest dump
                    # per_key evidence: the app window churned (rows added/removed) while
                    # text grew — search-as-you-type. (structure_id can't see this: it
                    # strips text + collapses adapter lists.)
                    if prev is not None and self._element_overlap(prev["snap"], new["snap"]) < 0.6:
                        self._typing_reactive = True
            elif self._typing_before is not None:
                self._finalize_field()  # blur / keyboard dismissed (Back)

    def _capture_before(self, prev: Optional[dict], cur, fallback: Snapshot) -> Snapshot:
        # Prefer the PRIOR cycle's same-field dump as the empty/initial baseline, so a fast
        # typer whose first focused dump already shows characters still diffs cleanly.
        if prev is not None:
            for n in prev["snap"].nodes:
                if is_editable(n) and field_identity(n) == field_identity(cur):
                    return prev["snap"]
        return fallback

    def _finalize_field(self) -> None:
        """Compute the just-finished field's set_text and stash it tagged with the current
        screen. MUST hold self.lock. Pure (detect_text_entry + list append) — no graph
        mutation. Redaction + coords-guard enforced here."""
        before, after, reactive = self._typing_before, self._typing_after, self._typing_reactive
        screen = self._typing_screen      # the field's FRESH screen id (not the raced current_id)
        ns = self._typing_namespace        # its namespace — the key _take_pending's bridge drains on
        self._typing_before = self._typing_after = self._typing_id = self._typing_screen = None
        self._typing_namespace = None
        self._typing_reactive = False
        if before is None or after is None:
            return
        action = detect_text_entry(before.nodes, after.nodes)
        if action is None:
            return
        if action.selector.kind == "coords":
            self.sink({"v": 1, "event_type": "unreplayable_field",
                       "screen": screen, "sensitive": action.sensitive})
            return
        if reactive:
            action.value = dict(action.value or {})
            action.value["replay_mode"] = "per_key"
        action.intent = "navigate"
        scr = self.graph.screen(screen) if screen else None
        # Prefer the namespace captured at the field's OWN fresh cycle; fall back to the graph
        # screen's namespace (settled path) or the raw id only if neither is available.
        ns_final = ns or (scr.namespace if scr else (screen or ""))
        # the builder's stage gate re-asserts redaction at ingest (lock-free append — this
        # method already holds self.lock, preserving today's discipline)
        if self._builder.stage_pending(action, screen, ns_final):
            self.sink({"v": 1, "event_type": "set_text", "screen": screen,
                       "field": (action.value or {}).get("param") or "field",
                       "sensitive": action.sensitive})  # NEVER the literal for a sensitive field

    def _commit_in_flight(self) -> None:
        """Finalize the currently-typed field, if any. Main-thread call sites."""
        with self.lock:
            if self._typing_before is not None:
                self._finalize_field()

    def _reset_typing(self) -> None:
        with self.lock:
            self._typing_before = self._typing_after = self._typing_id = self._typing_namespace = None
            self._typing_reactive = False

    def _take_pending(self, source: Optional[str]) -> list["Action"]:
        return self._builder.take_pending(source)

    def _drop_stale_pending(self, keep: Optional[str]) -> None:
        self._builder.drop_stale_pending(keep)

    # ---- identity-rekey repair (task #17b): a split/coarsen renames nodes old -> new ----
    def _apply_identity_rename(self, dec) -> None:
        """Delegate to the builder, which repairs ITS id-bearing state (current_id, pending
        tags, provisional strings) and calls back `_on_node_remap` for recorder-private state."""
        self._builder.apply_identity_rename(dec)

    def _on_node_remap(self, node_remap: dict) -> None:
        """Repair recorder-PRIVATE id state after a rename — the in-flight typing screen
        (the builder already repaired the staged pending tags), so a credential mid-type on a
        vanished node is not orphaned (Invariant #4). CONTRACT: invoked by the builder WITH
        the (shared) lock already held — atomic with the pending remap; must NOT re-acquire."""
        if self._typing_screen in node_remap:
            self._typing_screen = node_remap[self._typing_screen]

    @staticmethod
    def _is_ime_node(n) -> bool:
        # ONE identity rule, shared with capture (package / rid namespace / framework class —
        # never a package marker against a class path, which false-positived on app widgets
        # and silently swallowed real app taps inside the phantom "keyboard region").
        return is_ime_node(n)

    @staticmethod
    def _has_interactive(nodes) -> bool:
        # ONE affordance rule, shared with the builder's launch-anchor gate (RULE 2).
        return has_interactive(nodes)

    def _ime_bounds(self, nodes):
        """Bounding box of the soft-keyboard window, or None if no IME node is present.
        Detected by CLASS/resource-id package on ANY keyboard node (the window root
        android.inputmethodservice.SoftInputWindow always matches), so suppression keys on
        the keyboard REGION — not on classifying each key. Modern Gboard/OEM keyboards
        render keys as generic android.view.View with empty resource-id; a per-key class
        check misses them, but they all sit inside this region."""
        boxes = [n.bounds for n in nodes if self._is_ime_node(n)]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _source_snapshot(self, source_id: Optional[str], px: Optional[int] = None,
                         py: Optional[int] = None) -> Optional[Snapshot]:
        """The snapshot to bind a tap against: the freshest live dump IF it is still the
        source screen, else the arrival (settle) snapshot — ARBITRATED at the tap point:
        a frame with no interaction-plausible node at (px, py) is a lagging mid-load frame
        (the user acted on something it doesn't show), so the other candidate wins when it
        IS plausible there. Both suspect -> first candidate; the caller binds provisionally."""
        fresh = self._fresh
        src = self.graph.screen(source_id) if source_id else None
        candidates = []
        if fresh is not None and src is not None and fresh["ns"] == src.namespace:
            candidates.append(fresh["snap"])
        if self.current_snapshot is not None:
            candidates.append(self.current_snapshot)
        if not candidates:
            return None
        if px is not None:
            for snap in candidates:
                if plausible_bind_target(snap.nodes, px, py):
                    return snap
        return candidates[0]

    def _screen_from_fresh(self, fresh: dict, sid: str) -> Screen:
        # `sid` is the gate-resolved id (task #17b): for a refined family the live dump resolves
        # to the EXISTING twin (lookup_identity), never the raw coarse fp fresh["id"] which would
        # resurrect a coarse node beside the twins. chrome_digest stays None — only a real settle
        # in _enter sets a digest, so a low-confidence dump can never seed a false refinement.
        # value_bearing stays None DELIBERATELY (L3: the bit is a fact about a SETTLED dump's
        # hash; a single live dump cannot prove it — None routes the navigator to corroboration,
        # the honest default). adapter_dominant IS computed: list PRESENCE is a structural fact
        # a single dump can attest, and leaving it False would weaken the HW2 refined-twin guard.
        ns = fresh["ns"]
        pkg, _, activity = ns.partition("/")
        return Screen(
            id=sid, namespace=ns, structure_id=fresh["struct"],
            screen_type=_screen_type(ns, pkg or None), package=pkg or None,
            activity=activity or None, profile_name=fresh["profile_name"],
            fingerprint_confidence="low",  # inferred from a single live dump, not a full settle
            adapter_dominant=has_collapsing_list(fresh["xml"], focus_pkg=fresh.get("focus"))
            if fresh.get("xml") else False,
        )

    @staticmethod
    def _element_overlap(a: Snapshot, b: Snapshot) -> float:
        """Jaccard overlap of the two snapshots' (class, resource-id) element sets. A
        layout shift on the SAME screen (a collapsing toolbar, a scroll) keeps most
        elements → high overlap; a navigation to a DIFFERENT screen swaps them → low."""
        ka = {(n.cls, n.resource_id) for n in a.nodes}
        kb = {(n.cls, n.resource_id) for n in b.nodes}
        if not ka or not kb:
            return 0.0
        return len(ka & kb) / len(ka | kb)

    def _reconcile_current_screen(self) -> None:
        """Catch an UNRECORDED navigation: if the live screen no longer matches the node we
        think we're on (a tap that opened a new screen was dropped/missed — common on fast
        multi-tab apps), materialize the real current screen and point at it. The NEXT tap
        is then attributed to the screen it actually happened on, instead of fabricating a
        bogus direct edge from a stale node. The missing hop's ACTION can't be recovered;
        it is left as an honest gap (an `implicit_screen_change` event) for a hook or a
        re-record to fill — the graph stops lying about the topology.

        A change is only treated as a navigation when the element OVERLAP with the current
        screen is low — otherwise it is a same-screen layout shift (collapse/scroll), which
        _source_snapshot already handles, and minting a node there would split one screen."""
        fresh = self._fresh
        cur = self.graph.screen(self.current_id) if self.current_id else None
        if fresh is None or cur is None or fresh.get("stable", 0) < 1:
            return
        if not fresh.get("struct") or not cur.structure_id or fresh["struct"] == cur.structure_id:
            return  # same screen (structurally) — nothing dropped
        if self.current_snapshot is not None and \
                self._element_overlap(self.current_snapshot, fresh["snap"]) >= 0.4:
            return  # same screen, just a layout shift (collapse/scroll) — not a navigation
        # Resolve identity READ-ONLY (task #17b): for a refined twin family this returns the
        # EXISTING member, never a resurrected coarse F. None -> this dump matches no member of
        # a refined family (an unknown sibling) -> do NOT mint; leave current_id honestly as is.
        # The live refresher always carries xml; absent it (a hand-built fresh) fall back to the
        # raw coarse id (today's behavior — no family can exist without a prior real settle).
        if fresh.get("xml"):
            sid = lookup_identity(self.graph, fresh["ns"], fresh["xml"], fresh.get("focus"),
                                  fresh.get("cfg"))
            if sid is None:
                return
        else:
            sid = fresh["id"]
        screen = self._screen_from_fresh(fresh, sid)
        if screen.id == self.current_id:
            return
        # typed then navigated away WITHOUT a recorded submit tap (e.g. keyboard 'Go'):
        # commit the field, then the text typed on the old screen has no edge to ride on —
        # drop it with an honest marker rather than mis-attaching it to a later edge.
        self._commit_in_flight()
        self._drop_stale_pending(screen.id)
        self.graph.upsert_screen(screen)
        self.graph._profiles[screen.namespace] = screen.profile_name
        self.sink({
            "v": 1, "event_type": "implicit_screen_change",
            "from": self.current_id, "to": screen.id, "namespace": screen.namespace,
        })
        self.current_id = screen.id
        self.current_snapshot = fresh["snap"]
        # the §2.7/§2.8 region classification reads _settled_xml as the "before" frame; after a
        # reconcile to a NEW screen it must follow, or the next gesture is classified (and
        # in_region judged) against the PREVIOUS screen's dump.
        if fresh.get("xml"):
            self._settled_xml = fresh["xml"]
        if screen.screen_type == "homescreen" or is_launcher_namespace(screen.namespace):
            self._launching = False  # implicit return to the launcher abandons an in-flight launch

    # ---- record one tap, settle the target, add the edge ----
    def record_gesture(self, gesture: Gesture) -> Optional[Transition]:
        if self.paused or gesture.kind == "multi":
            return None
        # SECURITY: while the soft keyboard is up, suppress any tap inside the keyboard
        # REGION — a keystroke must NEVER become a 'click' edge (replayed in order it would
        # rebuild a password). Region-based (not per-key-class) so generic-class keys are
        # caught; gated on the keyboard being present (not on seeing the focused field), so
        # it holds even when the EditText is missing from the dump. App controls above the
        # keyboard (submit button, results) are outside the region -> never suppressed. The
        # set_text is recovered by the refresher's before/after diff instead.
        # Check EVERY faithful view of the current screen — the live refresher dump AND the
        # arrival snapshot (both NOT IME-stripped). Keying on ONE frame leaked a keystroke two
        # ways: (a) keying on _fresh alone left a ~refresh_interval hole right after navigation
        # (where _fresh is reset to None); (b) the tap-point bind ARBITRATION can bind against
        # the arrival frame when _fresh is non-plausible at the key, so a keyboard that is
        # present only in the arrival frame must still suppress. Invariant #4 errs toward
        # suppression: if the keyboard region containing the tap shows in ANY view, drop it.
        kx = scale_to_pixels(gesture.x, abs_min=self.profile.abs_x[0],
                             abs_max=self.profile.abs_x[1], screen=self.profile.display[0])
        ky = scale_to_pixels(gesture.y, abs_min=self.profile.abs_y[0],
                             abs_max=self.profile.abs_y[1], screen=self.profile.display[1])
        for snap in (self._fresh["snap"] if self._fresh is not None else None, self.current_snapshot):
            if snap is None:
                continue
            ime = self._ime_bounds(snap.nodes)
            if ime is None:
                continue
            landed = node_at(snap.nodes, kx, ky)
            if (ime[0] <= kx <= ime[2] and ime[1] <= ky <= ime[3]) or landed is None \
                    or self._is_ime_node(landed):
                return None
        # A non-keystroke tap = submit -> finalize the in-flight typed field so its set_text
        # is pending (tagged with the current screen) BEFORE the edge is built.
        self._commit_in_flight()
        # Reconcile: if a navigating tap was dropped, point at the screen we're actually on
        # before attributing this tap (else we'd fabricate a direct edge).
        self._reconcile_current_screen()
        source = self.current_id
        px = scale_to_pixels(
            gesture.x, abs_min=self.profile.abs_x[0], abs_max=self.profile.abs_x[1],
            screen=self.profile.display[0],
        )
        py = scale_to_pixels(
            gesture.y, abs_min=self.profile.abs_y[0], abs_max=self.profile.abs_y[1],
            screen=self.profile.display[1],
        )
        # Bind to the layout that was on screen at tap time, arbitrated AT THE TAP POINT —
        # a frame with nothing interaction-plausible there is a lagging mid-load frame, not
        # what the user saw (task #17a). When NO candidate frame is plausible at the point,
        # the bind is LOW confidence: the edge records through the same provisional channel
        # volatile sources already use — never a confident selector for an element the user
        # never aimed at (the on-device loading_container replay failure).
        source_snap = self._source_snapshot(source, px, py)
        plausible = source_snap is not None and plausible_bind_target(source_snap.nodes, px, py)
        try:
            action, needs = detect_action(gesture, source_snap, self.profile,
                                          bind_confidence="high" if plausible else "low")
        except ValueError:
            return None  # multi / unhandled — recorder survives
        src_node = node_at(source_snap.nodes, px, py)
        landed = src_node is not None
        if src_node is not None:
            # §7: persist the bind frame's element bounds + region membership — the reveal
            # rung's eligibility (§3.1) and container-binding (§3.3b) read these at replay.
            action.bounds = src_node.bounds
            action.in_region = self._builder._point_in_region(
                (src_node.bounds[0] + src_node.bounds[2]) // 2,
                (src_node.bounds[1] + src_node.bounds[3]) // 2, bounds=src_node.bounds)

        xml, ns, settled, focus = self._observe()
        after = self._builder.enter(xml, ns, settled, focus)
        if after.id != source:
            # moved to a new screen — drop the old live dump so the NEXT tap can't bind to
            # the previous screen's layout (matters when source/target share a namespace,
            # e.g. a single-Activity app); the refresher repopulates it for the new screen.
            self._fresh = None
        # Did this tap flip a checkable's state? Computed UNCONDITIONALLY from the tapped
        # node's own before/after diff — Android carries isChecked() on the click event,
        # ORTHOGONAL to navigation (see the builder's (2a) carrier decision).
        checked = detect_checkable_entry(src_node, after.snapshot.nodes)
        # Everything from here is the GRAPH-BUILD CORE — one commit path shared with the v2
        # ingestion seam (launch anchor, set_checked staging, §2.7/§2.8 classification, edge
        # build + dedup). The human path supplies full pixel context via BindContext.
        return self._builder.commit_transition(
            action=action, after=after,
            bind=BindContext(px=px, py=py, end=action.end,
                             bounds=(src_node.bounds if src_node is not None else None),
                             landed=landed),
            checked=checked, needs_confirmation=needs)


    # ---- code-side control (§3.3) ----
    def pause(self) -> None:
        self._commit_in_flight()  # commit in-flight text before pausing capture
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self._reset_typing()  # discard any stale typing baseline from before the pause
        sid, snap, _, dec = self._enter()  # re-anchor; the human may have moved the device
        # the re-anchor settle may SPLIT a pre-existing coarse twin (a credential staged before
        # the pause is tagged with the vanished F) — apply the rename so it is not dropped.
        self._apply_identity_rename(dec)
        self.current_id, self.current_snapshot = sid, snap
        self._settled_xml = self._last_enter_xml

    def confirm_edge(self, edge_id: str) -> None:
        if edge_id in self.provisional:
            self.provisional.remove(edge_id)

    def reject_edge(self, edge_id: str) -> None:
        if edge_id in self.provisional:
            self.provisional.remove(edge_id)
        u, rest = edge_id.split("->")
        v, key = rest.split("#")
        if self.graph.g.has_edge(u, v, int(key)):
            self.graph.g.remove_edge(u, v, int(key))

    def correct_edge(self, edge_id: str, selector) -> None:
        u, rest = edge_id.split("->")
        v, key = rest.split("#")
        if self.graph.g.has_edge(u, v, int(key)):
            self.graph.g[u][v][int(key)]["action"].selector = selector
            self.confirm_edge(edge_id)

    def mark_same(self, keep_id: str, dup_id: str) -> None:
        """Human under-merge ground truth: these two ids are one screen (§7). When the two are
        REFINED twins of one family, the human is saying the chrome-based refinement over-split —
        so coarsen the WHOLE family back to its coarse node and BLACKLIST it (task #17b), else the
        very next settled visit re-mints the merged-away twin and silently undoes the human's call.
        Otherwise it is an ordinary under-merge of two distinct ids."""
        keep = self.graph.screen(keep_id)
        dup = self.graph.screen(dup_id)
        F = keep.coarse_id if keep is not None else None
        if F is not None and dup is not None and dup.coarse_id == F:
            members = [n for n in self.graph.g.nodes if self.graph.screen(n).coarse_id == F]
            node_remap = {m: F for m in members}
            edge_remap = coarsen_family(self.graph, F, members)
            # repair EVERY holder of a vanished member id — current_id, the pending typing
            # tags + in-flight typing screen (else a staged credential is orphaned and
            # dropped, Invariant #4), and the provisional edge strings.
            self._builder.remap_holders(node_remap, edge_remap)
            return
        self.graph.merge_screens(keep_id, dup_id)
        if self.current_id == dup_id:
            self.current_id = keep_id
