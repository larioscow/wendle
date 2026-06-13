from __future__ import annotations

from enum import IntEnum
from typing import Optional

from wendle.fingerprint.compose import (
    COMPOSE_PROFILE,
    LAUNCHER_PROFILE,
    VIEW_PROFILE,
)
from wendle.fingerprint.signature import (
    adapter_list_dominant,
    chrome_digest,
    fingerprint,
    refined_id,
    structure_id,
)
from wendle.models import Screen, Selector

_PROFILES = {
    "view": VIEW_PROFILE,
    "compose": COMPOSE_PROFILE,
    "launcher": LAUNCHER_PROFILE,
    "volatile": VIEW_PROFILE,
}


class Tier(IntEnum):
    """How confidently an observed screen matches a recorded one (§ DroidBot two-level
    state identity). Ordered: a higher tier is a stronger match.

    EXACT       full fingerprint match (incl. Compose text) — trust completely.
    STRUCTURE   text-free structure_id match — right screen, content may have changed.
    WEAK        namespace match + a recorded resource_id probe still resolves.
    UNVERIFIABLE namespace plausibly right but nothing distinguishes it from a sibling
                (no probe, or a content-free adapter-list screen) — honest STOP, never
                a confident arrival.
    MISMATCH    wrong screen.
    """

    MISMATCH = 0
    UNVERIFIABLE = 1
    WEAK = 2
    STRUCTURE = 3
    EXACT = 4


def config_for(target: Screen):
    """Rehydrate the record-time profile by NAME — never re-detect at navigate."""
    return _PROFILES.get(target.profile_name, VIEW_PROFILE)


def observed_matches_id(observed_ns: str, observed_xml: str, target: Screen,
                        focus_pkg: Optional[str], config) -> bool:
    """True when the observed screen reproduces `target`'s id (task #17b). For a normal screen
    that is the coarse fingerprint match. For a REFINED twin (coarse_id != None) the id is
    refined_id(coarse_fp, chrome_digest), so reproduce it from the live coarse fingerprint + live
    chrome digest — the same value-evidence class as a Compose text fingerprint, distinguishing
    sibling pages a text-free fingerprint cannot."""
    F = fingerprint(observed_ns, observed_xml, config, focus_pkg)
    if F == target.id:
        return True
    if target.coarse_id is not None:
        d = chrome_digest(observed_xml, config, focus_pkg)
        return d is not None and refined_id(F, d) == target.id
    return False


def most_reliable_selector(actions) -> Optional[Selector]:
    """The probe used to backstop a namespace-only match — resource_id only, and only
    from a navigate-intent action (a reveal/probe scroll selector is not a presence
    test), NEVER borrowed/volatile text (a localized label is not a reliable probe)."""
    for a in actions:
        if a.selector.kind == "resource_id" and getattr(a, "intent", "navigate") == "navigate":
            return a.selector
    return None


def verify_match(
    observed_xml: str,
    observed_ns: str,
    target: Screen,
    driver,
    focus_pkg: Optional[str] = None,
) -> Tier:
    """Grade how well the observed screen matches `target` (§ verify tiers).

    EXACT first (fast, strongest). Many real screens are dynamic (feeds, Compose
    greetings) so their full fingerprint won't reproduce on replay — fall back to the
    text-free STRUCTURE tier, then to a namespace + resource_id presence probe (WEAK).
    A structure match on a content-free adapter-list screen, or a namespace match with
    no probe, is UNVERIFIABLE — the caller stops honestly instead of claiming arrival.
    """
    if observed_matches_id(observed_ns, observed_xml, target, focus_pkg, config_for(target)):
        return Tier.EXACT
    if target.structure_id and structure_id(observed_ns, observed_xml, focus_pkg) == target.structure_id:
        if observed_ns == target.namespace and adapter_list_dominant(observed_xml, focus_pkg=focus_pkg):
            return Tier.UNVERIFIABLE
        return Tier.STRUCTURE
    if observed_ns != target.namespace:
        return Tier.MISMATCH
    probe = most_reliable_selector(target.actions)
    if probe is None:
        return Tier.UNVERIFIABLE  # namespace alone passes on every screen of a 1-Activity app
    return Tier.WEAK if driver.xpath_exists(probe) else Tier.MISMATCH
