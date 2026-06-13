"""The ONE screen-identity gate (task #17b): collision-driven twin refinement, APE's CEGAR shape.

A screen's confident identity is its namespace + text-free widget skeleton (today's coarse
fingerprint). The recorder refines a structure-twin family apart — ONLY after two settled captures
with the same coarse identity are OBSERVED to differ by their stable shallow chrome (the digest).
Until a collision is observed, twins stay merged and every existing honesty rule applies; refinement
only ever splits what evidence proved distinct, and a family that proves volatile is coarsened back
and blacklisted, restoring today's exact behavior.

ONLY the recorder mints identity through this gate; navigator/replay load a frozen graph and never
refine. Adding a future refinement rung (rid-set digest, interactable-set, sibling index) is one new
leaf here, never an if/else elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from wendle.fingerprint.signature import (
    chrome_digest,
    fingerprint,
    is_launcher_namespace,
    refined_id,
    structure_id,
)

# A family is coarsened back and blacklisted past this many distinct twins. It is an order of
# magnitude above any plausible real twin family (Pixel Settings is ~15-20 sub-pages) — a family
# SIZE cap tuned near real sizes would un-fix the motivating app (adversarial finding F3), so this
# bounds only RUNAWAY chrome churn (a screen minting a fresh ghost digest every visit). Churn-keyed
# coarsening (a member whose digest is never re-observed) is the future hardening; this is the
# robust v1 backstop.
FAMILY_MAX = 32


@dataclass
class IdentityDecision:
    """What identity a settled capture resolves to, plus the carriers the caller stamps and any
    rename it must reconcile. The gate DECIDES and mutates only EXISTING nodes; the recorder's
    _enter is the SOLE minter of the returned-id node (so its rich presentation fields — profile,
    screen_type — are never dropped by an upsert union). `chrome_digest`/`coarse_id` are the
    refinement carriers _enter copies onto the node it mints (coarse_id != None marks a refined
    twin; the coarse node carries its digest so the NEXT visit can detect a collision).

    `node_remap = {old_id: new_id, ...}` when the gate renamed EXISTING nodes — ONE entry for a
    SPLIT (the coarse F became refined T_old) or N entries for a COARSEN (every family member
    merged back into coarse F). A caller holding any old_id (source, current_id, pending typing
    tags) must remap it (the split/coarsen-while-source lifecycle blocker). `remap` is the
    edge-key remap (for provisional 'u->v#k' strings). `coarsened` carries the coarse fp when a
    family was merged back (informational)."""

    id: str
    chrome_digest: Optional[str] = None
    coarse_id: Optional[str] = None
    node_remap: Optional[dict] = None
    remap: Optional[dict] = None
    coarsened: Optional[str] = None


def lookup_identity(graph, namespace, xml, focus_pkg, config) -> Optional[str]:
    """READ-ONLY identity for a low-confidence single dump (the refresher's reconcile path):
    return the id this screen ALREADY has, or None meaning 'do NOT mint'. NEVER mutates the
    graph — a refresher guess must never SPLIT a family or RESURRECT a coarse node beside its
    refined twins (the F4 identity fork). Only a real settle in _enter may refine.

    Resolution: launcher -> the home id; an existing coarse node F -> F (unrefined/exhausted/
    pre-collision); an existing refined twin for this chrome -> that twin; F has a refined family
    but this chrome matches no member -> None (skip — never resurrect coarse F); genuinely new
    (no node, no family) -> F (reconcile may mint a fresh coarse node, as today)."""
    if is_launcher_namespace(namespace):
        return fingerprint(namespace, xml)
    F = fingerprint(namespace, xml, config, focus_pkg)
    if graph.screen(F) is not None:
        return F
    digest = chrome_digest(xml, config, focus_pkg)
    if digest is not None and graph.screen(refined_id(F, digest)) is not None:
        return refined_id(F, digest)
    if _family(graph, F):
        return None  # a refined family exists but this dump matches none -> do not fork
    return F


def resolve_identity(graph, namespace, xml, focus_pkg, settled, config) -> IdentityDecision:
    # 1. unsettled -> volatile (today): a never-settled screen is identified by coarse structure,
    #    never refined (its chrome is mid-transition and meaningless).
    if not settled:
        return IdentityDecision(id="V" + structure_id(namespace, xml, focus_pkg)[1:])

    # 2. launcher -> the single 'home' node (today): no structure tier, no chrome path.
    if is_launcher_namespace(namespace):
        return IdentityDecision(id=fingerprint(namespace, xml))

    F = fingerprint(namespace, xml, config, focus_pkg)
    digest = chrome_digest(xml, config, focus_pkg)  # None = no stable chrome -> NEVER refinable (F1)

    # 3. blacklisted family -> stay coarse, exactly as today (no digest carried -> never refines).
    if graph.is_twin_exhausted(F):
        return IdentityDecision(id=F)

    existing = graph.screen(F)
    if existing is not None:
        # 4. a coarse node F already exists.
        if existing.chrome_digest is None:
            existing.chrome_digest = digest  # backfill (legacy or first-digest visit); union
            return IdentityDecision(id=F, chrome_digest=digest)
        if digest is None or digest == existing.chrome_digest:
            # empty digest can't refine (F1); same chrome = revisit
            return IdentityDecision(id=F, chrome_digest=existing.chrome_digest)
        # 4c. a COLLISION is observed: two settled captures, same coarse F, different chrome.
        return _split(graph, F, existing, digest)

    # 5. F is not a node, but a refined family for F may already exist.
    members = _family(graph, F)
    if members:
        if digest is None:
            return IdentityDecision(id=F)  # empty chrome can't pick a twin -> stay coarse (F1)
        T = refined_id(F, digest)
        if graph.screen(T) is not None:
            return IdentityDecision(id=T, chrome_digest=digest, coarse_id=F)  # revisit a twin
        if len(members) >= FAMILY_MAX:
            return _coarsen_family(graph, F, members)
        return IdentityDecision(id=T, chrome_digest=digest, coarse_id=F)  # mint a new twin (_enter)

    # 6. a brand-new screen.
    return IdentityDecision(id=F, chrome_digest=digest)


# ---- leaves (mutate ONLY existing nodes; _enter mints the returned-id node) ----------------------

def _split(graph, F, existing, digest) -> IdentityDecision:
    """The coarse node E (id F) becomes a refined twin: rekey F -> T_old (preserving E's full
    Screen) and mark it refined. The new twin T_new is NOT minted here — _enter mints it from the
    returned carriers. Returns the rename + edge-key remap so the caller fixes a stale source,
    current_id, typing tags, and provisional strings."""
    T_old = refined_id(F, existing.chrome_digest)
    remap = graph.rekey_screen(F, T_old)
    graph.screen(T_old).coarse_id = F  # E keeps its chrome_digest; now marked refined
    T_new = refined_id(F, digest)
    return IdentityDecision(id=T_new, chrome_digest=digest, coarse_id=F,
                            node_remap={F: T_old}, remap=remap)


def _family(graph, F):
    return [nid for nid in graph.g.nodes if graph.screen(nid).coarse_id == F]


def coarsen_family(graph, F, members) -> dict:
    """Merge the whole refined family back into ONE coarse node F and blacklist it, restoring
    today's behavior. Clears BOTH chrome_digest and coarse_id atomically (a stale coarse_id would
    make verify compare a refined id to a coarse one -> permanently unsatisfiable EXACT). Returns
    the edge-key remap. Shared by the runaway-churn gate leaf AND a human twin-merge (mark_same)."""
    keep = members[0]
    edge_remap = dict(graph.rekey_screen(keep, F))
    node = graph.screen(F)
    node.chrome_digest = None
    node.coarse_id = None
    for m in members[1:]:
        edge_remap.update(graph.merge_screens(F, m) or {})
    graph.mark_twin_exhausted(F)
    # CHAIN-RESOLVE: a successive merge re-keys an edge a PRIOR merge already moved, so the raw
    # composed map points an original key at an intermediate (now-dead) key. Follow each value to
    # its terminal so a single _remap_provisional lookup lands on the LIVE edge (re-verification:
    # else a provisional inter-member edge becomes an unrejectable zombie).
    def _terminal(k):
        seen = set()
        while k in edge_remap and k not in seen:
            seen.add(k)
            k = edge_remap[k]
        return k
    return {k: _terminal(v) for k, v in edge_remap.items()}


def _coarsen_family(graph, F, members) -> IdentityDecision:
    """Runaway-churn gate leaf: coarsen the family + return the node/edge remap so the recorder
    repairs a source/current_id/pending tag on ANY member (coarsen-while-source)."""
    edge_remap = coarsen_family(graph, F, members)
    return IdentityDecision(id=F, node_remap={m: F for m in members}, remap=edge_remap, coarsened=F)
