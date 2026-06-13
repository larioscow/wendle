from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Callable, Optional

import networkx as nx

from wendle.fingerprint.signature import SIGNATURE_VERSION, is_launcher_namespace


class StaleRecordingError(ValueError):
    """The recording was captured under an older identity (signature) version.

    Region-bearing screens' ids changed meaning (lazy-region design §2.6); replaying or
    navigating such a graph would yield a silent app-wide off_graph storm — so the verbs
    refuse INSTANTLY and TYPED instead, naming the re-record requirement."""


def check_signature_version(graph) -> None:
    """Typed, instant refusal for graphs whose ids this build cannot reproduce (§2.6)."""
    v = getattr(graph, "signature_version", 1)
    if v != SIGNATURE_VERSION:
        raise StaleRecordingError(
            f"recording carries signature_version {v}, but this build computes version "
            f"{SIGNATURE_VERSION} screen ids — re-record the capture (region-bearing screens' "
            f"identities changed; see the design spec §2.6)")
from wendle.models import (
    Action,
    DeviceProfile,
    ForceAction,
    Screen,
    Selector,
    Transition,
)


def _selector_to_dict(s: Selector) -> dict:
    value = list(s.value) if isinstance(s.value, tuple) else s.value
    return {"kind": s.kind, "value": value}


def _selector_from_dict(d: dict) -> Selector:
    value = tuple(d["value"]) if d["kind"] == "coords" else d["value"]
    return Selector(kind=d["kind"], value=value)


def _action_to_dict(a: Action) -> dict:
    return {
        "selector": _selector_to_dict(a.selector),
        "action_type": a.action_type,
        "value": a.value,  # {"param": ...} or {"text": ...} or None — never a secret literal
        "sensitive": a.sensitive,
        "replayability": a.replayability,
        "end": list(a.end) if a.end else None,
        "intent": a.intent,
        "bounds": list(a.bounds) if a.bounds else None,
        "in_region": a.in_region,
    }


def _action_from_dict(d: dict) -> Action:
    return Action(
        selector=_selector_from_dict(d["selector"]),
        action_type=d["action_type"],
        value=d.get("value"),
        sensitive=d.get("sensitive", False),
        replayability=d.get("replayability", "high"),
        end=tuple(d["end"]) if d.get("end") else None,
        intent=d.get("intent", "navigate"),
        bounds=tuple(d["bounds"]) if d.get("bounds") else None,
        in_region=d.get("in_region", False),
    )


def _force_to_dict(f: Optional[ForceAction]) -> Optional[dict]:
    if f is None:
        return None
    return {"kind": f.kind, "value": f.value, "verified_fp": f.verified_fp,
            "provenance": f.provenance}


def _force_from_dict(d: Optional[dict]) -> Optional[ForceAction]:
    if d is None:
        return None
    return ForceAction(kind=d["kind"], value=d["value"], verified_fp=d.get("verified_fp"),
                       provenance=d.get("provenance", "unknown"))


def _screen_to_dict(s: Screen) -> dict:
    return {
        "id": s.id,
        "namespace": s.namespace,
        "structure_id": s.structure_id,
        "chrome_digest": s.chrome_digest,
        "coarse_id": s.coarse_id,
        "adapter_dominant": s.adapter_dominant,
        "value_bearing": s.value_bearing,
        "screen_type": s.screen_type,
        "package": s.package,
        "activity": s.activity,
        "profile_name": s.profile_name,
        "fingerprint_confidence": s.fingerprint_confidence,
        "volatile": s.volatile,
        "actions": [_action_to_dict(a) for a in s.actions],
        "intra_actions": [_action_to_dict(a) for a in s.intra_actions],
        "force_action": _force_to_dict(s.force_action),
        "hierarchy": s.hierarchy,  # None by default (PII)
    }


def _screen_from_dict(d: dict) -> Screen:
    return Screen(
        id=d["id"],
        namespace=d["namespace"],
        structure_id=d.get("structure_id"),
        chrome_digest=d.get("chrome_digest"),  # legacy graphs -> None -> behaves as today
        coarse_id=d.get("coarse_id"),
        adapter_dominant=d.get("adapter_dominant", False),
        value_bearing=d.get("value_bearing"),  # legacy -> None -> NOT value-bearing (honest default)
        screen_type=d.get("screen_type", "app"),
        package=d.get("package"),
        activity=d.get("activity"),
        profile_name=d.get("profile_name", "view"),
        fingerprint_confidence=d.get("fingerprint_confidence", "high"),
        volatile=d.get("volatile", False),
        actions=[_action_from_dict(a) for a in d.get("actions", [])],
        intra_actions=[_action_from_dict(a) for a in d.get("intra_actions", [])],
        force_action=_force_from_dict(d.get("force_action")),
        hierarchy=d.get("hierarchy"),
    )


class Graph:
    """The navigation multigraph (§4): screens are nodes, transitions are parallel
    edges. Hooks are kept SEPARATELY (keyed by id) and are NEVER serialized."""

    def __init__(self):
        self.g = nx.MultiDiGraph()
        # The identity-function version this graph's screen ids were computed under. A freshly
        # built graph is current by construction; from_json restores the RECORDED version and
        # replay/navigate refuse a stale one typed (§2.6 — never a silent off_graph storm).
        self.signature_version: int = SIGNATURE_VERSION
        self.device_profile: Optional[DeviceProfile] = None
        self._profiles: dict[str, str] = {}  # namespace -> profile NAME (stored, never reverse-derived)
        self.hooks: dict[str, list[Callable]] = {}  # NEVER serialized
        # CHRONOLOGICAL order of committed transitions (source, target, key). nx.edges() groups
        # by source node, so it is NOT recording order once a screen is revisited; this list is
        # the faithful linear trace the replay engine re-enacts.
        self._edge_order: list[tuple[str, str, int]] = []
        # Coarse fingerprints whose twin family proved volatile (chrome churn minted ghost
        # twins, or a human merged two members): refinement is abandoned for these and they
        # behave exactly as today. Serialized so the blacklist survives a reload (task #17b).
        self._twin_exhausted: set[str] = set()

    def has_suspect_self_loop(self, node_id: str) -> bool:
        """True when `node_id` carries a record-time §2.8 suspect self-loop edge — a tap that
        collapsed a region and landed on a logically-different screen aliasing to this id. Its
        identity is known-ambiguous, so the navigator never grants confident arrival here. (The
        edge is a self-loop, dropped from routable_subgraph; this NODE query is the reachable
        form of the §2.8 honesty cap.)"""
        if node_id not in self.g.nodes:
            return False
        return any(data.get("suspect_self_loop")
                   for _u, _v, data in self.g.out_edges(node_id, data=True))

    def mark_twin_exhausted(self, coarse_fp: str) -> None:
        self._twin_exhausted.add(coarse_fp)

    def is_twin_exhausted(self, coarse_fp: str) -> bool:
        return coarse_fp in self._twin_exhausted

    def upsert_screen(self, s: Screen) -> bool:
        """Add a screen; return True if newly added. On revisit, keep the node and
        union its actions (new selectors discovered on later visits)."""
        if s.id in self.g.nodes:
            existing: Screen = self.g.nodes[s.id]["screen"]
            seen = {(a.selector.kind, a.selector.value) for a in existing.actions}
            for a in s.actions:
                if (a.selector.kind, a.selector.value) not in seen:
                    existing.actions.append(a)
            seen_intra = {(a.selector.kind, a.selector.value, a.intent) for a in existing.intra_actions}
            for a in s.intra_actions:
                if (a.selector.kind, a.selector.value, a.intent) not in seen_intra:
                    existing.intra_actions.append(a)
            if existing.force_action is None and s.force_action is not None:
                existing.force_action = s.force_action
            return False
        self.g.add_node(s.id, screen=s)
        return True

    def screen(self, screen_id: str) -> Optional[Screen]:
        node = self.g.nodes.get(screen_id)
        return node["screen"] if node else None

    def add_transition(self, t: Transition) -> str:
        key = self.g.add_edge(
            t.source,
            t.target,
            action=t.action,
            weight=t.weight,
            action_class=t.action_class,
            pre_actions=t.pre_actions,
            needs_confirmation=t.needs_confirmation,
            suspect_self_loop=t.suspect_self_loop,
            global_affordance=t.global_affordance,
            settled=t.settled,
            landed_on_real_element=t.landed_on_real_element,
        )
        self._edge_order.append((t.source, t.target, key))
        return f"{t.source}->{t.target}#{key}"

    def ordered_transitions(self):
        """Yield (source, target, key, data) for every committed transition in CHRONOLOGICAL
        order — the faithful linear trace for replay (unlike nx edges(), which group by node)."""
        for (u, v, key) in self._edge_order:
            data = self.g[u][v][key]
            yield u, v, key, data

    def merge_screens(self, keep_id: str, dup_id: str) -> dict:
        """Under-merge handling (§7): the human confirmed two ids are ONE screen —
        redirect dup's edges to keep, union actions, drop dup. The human's revisit
        is the ground truth; no auto-refine. `_edge_order` (the faithful replay trace)
        is remapped in place, so every redirected edge keeps its chronological slot
        and the trace/save survive the merge. Returns the edge-key remap {(u,v,k):
        (u',v',k')} so callers can fix id-bearing edge-key state (provisional strings)."""
        if keep_id == dup_id or keep_id not in self.g.nodes or dup_id not in self.g.nodes:
            return {}
        remap: dict[tuple[str, str, int], tuple[str, str, int]] = {}
        for u, _v, k, data in list(self.g.in_edges(dup_id, keys=True, data=True)):
            nu = keep_id if u == dup_id else u
            nk = self.g.add_edge(nu, keep_id, **dict(data))
            remap[(u, dup_id, k)] = (nu, keep_id, nk)
        for _u, v, k, data in list(self.g.out_edges(dup_id, keys=True, data=True)):
            if v == dup_id:
                continue  # the dup self-loop was already redirected by the in_edges pass
            nk = self.g.add_edge(keep_id, v, **dict(data))
            remap[(dup_id, v, k)] = (keep_id, v, nk)
        keep: Screen = self.g.nodes[keep_id]["screen"]
        dup: Screen = self.g.nodes[dup_id]["screen"]
        seen = {(a.selector.kind, a.selector.value) for a in keep.actions}
        for a in dup.actions:
            if (a.selector.kind, a.selector.value) not in seen:
                keep.actions.append(a)
        seen_intra = {(a.selector.kind, a.selector.value, a.intent) for a in keep.intra_actions}
        for a in dup.intra_actions:
            if (a.selector.kind, a.selector.value, a.intent) not in seen_intra:
                keep.intra_actions.append(a)
        self.g.remove_node(dup_id)
        self._edge_order = [remap.get(e, e) for e in self._edge_order]
        return remap

    def rekey_screen(self, old_id: str, new_id: str) -> dict:
        """Rename a node id in place, preserving its Screen (and updating Screen.id), all of
        its parallel in/out edges, and their chronological slots in `_edge_order`. This is what
        a twin SPLIT does to the existing member (coarse F -> refined T). Returns the edge-key
        remap {(u,v,k): (u',v',k')} so callers (the recorder) can fix any id-bearing state that
        references the old edge keys (e.g. provisional 'u->v#k' strings). No-op if old==new or
        old is absent; raises if new_id already exists (a rekey must not silently merge)."""
        if old_id == new_id or old_id not in self.g.nodes:
            return {}
        if new_id in self.g.nodes:
            raise ValueError(f"rekey target already exists: {new_id}")
        screen: Screen = self.g.nodes[old_id]["screen"]
        screen.id = new_id
        # ForceAction is frozen and verified_fp pins the OLD id (it is consumed as the flow
        # start_id and the launch winning-rung cache key) — rewrite it to the new id here, at
        # the rekey layer, so every caller (twin split, family coarsen, future) is covered.
        fa = screen.force_action
        if fa is not None and fa.verified_fp == old_id:
            screen.force_action = dataclasses.replace(fa, verified_fp=new_id)
        self.g.add_node(new_id, screen=screen)
        remap: dict[tuple[str, str, int], tuple[str, str, int]] = {}
        for u, _v, k, data in list(self.g.in_edges(old_id, keys=True, data=True)):
            nu = new_id if u == old_id else u  # a self-loop moves both ends
            nk = self.g.add_edge(nu, new_id, **dict(data))
            remap[(u, old_id, k)] = (nu, new_id, nk)
        for _u, v, k, data in list(self.g.out_edges(old_id, keys=True, data=True)):
            if v == old_id:
                continue  # self-loop already handled by the in_edges pass
            nk = self.g.add_edge(new_id, v, **dict(data))
            remap[(old_id, v, k)] = (new_id, v, nk)
        self.g.remove_node(old_id)
        self._edge_order = [remap.get(e, e) for e in self._edge_order]
        return remap

    def _is_launcher_node(self, node_id: str) -> bool:
        s = self.screen(node_id)
        return s is not None and (
            s.screen_type == "homescreen" or is_launcher_namespace(s.namespace)
        )

    def routable_subgraph(self) -> nx.MultiDiGraph:
        """Edges the Navigator may walk (§ DroidBot routing). Drops:
          * self-loops — non-state-changing, kept only as record-time data; and
          * launcher-incident edges — the launcher is reached via its keyevent anchor
            and apps via am_start anchors, never by replaying a fragile home-icon edge.
        Cross-package edges are KEPT but stamped cross_app=True so the navigator treats
        them as re-anchor checkpoints (dropping them would erase the only recorded path
        into a share/OAuth-only target)."""
        sub = nx.MultiDiGraph()
        sub.add_nodes_from(self.g.nodes(data=True))
        for u, v, k, data in self.g.edges(keys=True, data=True):
            if u == v or self._is_launcher_node(u) or self._is_launcher_node(v):
                continue
            su, sv = self.screen(u), self.screen(v)
            cross = bool(su and sv and su.package and sv.package and su.package != sv.package)
            sub.add_edge(u, v, key=k, **{**data, "cross_app": cross})
        return sub

    def anchors(self) -> list[str]:
        return [
            sid
            for sid, data in self.g.nodes(data=True)
            if data["screen"].force_action and data["screen"].force_action.verified
        ]

    # ---- persistence (structure-only; never callables, never secret literals) ----

    def to_json(self) -> str:
        transitions = []
        for u, v, key, data in self.ordered_transitions():  # CHRONOLOGICAL, the faithful trace
            transitions.append(
                {
                    "source": u,
                    "target": v,
                    "key": key,
                    "action": _action_to_dict(data["action"]),
                    "weight": data.get("weight", 1.0),
                    "action_class": data.get("action_class", "navigate"),
                    "pre_actions": [_action_to_dict(a) for a in data.get("pre_actions", [])],
                    "needs_confirmation": data.get("needs_confirmation", False),
                    "suspect_self_loop": data.get("suspect_self_loop", False),
                    "global_affordance": data.get("global_affordance", False),
                    "settled": data.get("settled", False),
                    "landed_on_real_element": data.get("landed_on_real_element", False),
                }
            )
        payload = {
            "v": 1,
            "signature_version": self.signature_version,
            "device_profile": (
                json.loads(self.device_profile.to_json()) if self.device_profile else None
            ),
            "fingerprint_config": dict(self._profiles),
            "twin_exhausted": sorted(self._twin_exhausted),  # the refinement blacklist (task #17b)
            "screens": [_screen_to_dict(self.g.nodes[n]["screen"]) for n in self.g.nodes],
            "transitions": transitions,
        }
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, blob: str) -> "Graph":
        d = json.loads(blob)
        graph = cls()
        # legacy recordings (pre-region identity) carry no version field -> 1 -> refused typed
        graph.signature_version = d.get("signature_version", 1)
        if d.get("device_profile"):
            graph.device_profile = DeviceProfile.from_json(json.dumps(d["device_profile"]))
        graph._profiles = dict(d.get("fingerprint_config", {}))
        graph._twin_exhausted = set(d.get("twin_exhausted", []))  # legacy graphs -> empty
        for sd in d.get("screens", []):
            graph.g.add_node(sd["id"], screen=_screen_from_dict(sd))
        for td in d.get("transitions", []):
            key = graph.g.add_edge(
                td["source"],
                td["target"],
                key=td.get("key"),
                action=_action_from_dict(td["action"]),
                weight=td.get("weight", 1.0),
                action_class=td.get("action_class", "navigate"),
                pre_actions=[_action_from_dict(a) for a in td.get("pre_actions", [])],
                needs_confirmation=td.get("needs_confirmation", False),
                suspect_self_loop=td.get("suspect_self_loop", False),
                global_affordance=td.get("global_affordance", False),
                settled=td.get("settled", False),
                landed_on_real_element=td.get("landed_on_real_element", False),
            )
            # preserve the JSON's chronological transition order across the round-trip
            graph._edge_order.append((td["source"], td["target"], key))
        return graph

    def save(self, path) -> None:
        path = Path(path)
        path.write_text(self.to_json())
        os.chmod(path, 0o600)  # behavioral map + structure = sensitive (§3.4)
