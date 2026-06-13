"""VERIFY-BY-AFFORDANCE — content-independent confirmation that tapping a global-nav affordance
A reached A's section. The honesty gate for routing content-DRIFTING sections (a live-clock tab)
whose fingerprint no longer matches any recorded screen.

Pure function over (pre_tap_xml, post_tap_settled_xml, A's value, focus_pkg, target_pkg). The
caller settles BEFORE calling (no mid-transition read). Every gate below closes a confident-wrong
path the 0/3 adversarial review found:
  - FOCUS: live package == target package (before+after) — a foreign app rendering A's string
    must not verify.
  - CONTAINER-SCOPED + UNIQUE: A must be the UNIQUE content-desc member of a detected nav
    container — a body label matching A, or a duplicate, does not count.
  - AFFIRMATIVE SELECTED: A's member (or its container-row ancestor) is selected='true'. NEVER
    clickable='false' (a plain/disabled label is clickable=false — indistinguishable).
  - CONTENT CHANGED: the non-nav content differs from the pre-tap dump (a real navigation, not a
    drawer staying open / an optimistic indicator flip).
Verdict: 'arrived' (all gates) | 'unverified' (in-app + A present in a container but no
affirmative selected signal this app exposes — honest, never confident) | 'no'.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _xml_fromstring

from wendle.capture.affordance import nav_container_members, _parse_bounds


def _focus_pkg_ok(xml: str, target_pkg: str) -> bool:
    # the post-tap dump's nodes are in the target package (cheap, robust: the root app package)
    m = re.search(r'package="([^"]+)"', xml or "")
    return bool(m) and m.group(1) == target_pkg


def _content_digest(xml: str, member_bounds) -> str:
    """A hash of the screen's NON-nav text/desc content (everything outside the nav bar members)
    — so 'content changed' means the section body actually navigated, not just the tab indicator."""
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return ""
    parts = []
    for el in root.iter("node"):
        b = _parse_bounds(el.get("bounds"))
        if b is not None and b in member_bounds:
            continue  # skip nav-bar members themselves
        # skip anything inside a member's column (the bar's icons/labels)
        if b is not None and any(mb[0] <= b[0] and mb[1] <= b[1] and mb[2] >= b[2] and mb[3] >= b[3]
                                 for mb in member_bounds):
            continue
        t, d = el.get("text", ""), el.get("content-desc", "")
        if t or d:
            parts.append(f"{t}{d}")
    return hashlib.sha1("".join(parts).encode()).hexdigest()


def current_section(xml: str, affordance_value: str, focus_pkg: Optional[str],
                    target_pkg: Optional[str]) -> str:
    """Is the (SETTLED) screen `xml` currently SHOWING affordance A's section — i.e. is A the
    active/selected member of a real nav container, in the target app? 'yes' | 'unverified'
    (A present in a bar but this app exposes no affirmative selected signal) | 'no'. The caller
    must pass a SETTLED frame (the content has loaded; a mid-transition optimistic-selected
    frame would lie). No tap is implied — this answers 'are we ON A's section right now'."""
    if target_pkg is not None:
        if focus_pkg is not None and focus_pkg != target_pkg:
            return "no"
        if not _focus_pkg_ok(xml, target_pkg):
            return "no"
    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError):
        return "no"
    members = nav_container_members(xml)
    if not members:
        return "no"
    a_nodes = [el for el in root.iter("node")
               if el.get("content-desc") == affordance_value
               and _parse_bounds(el.get("bounds")) in members]
    if len(a_nodes) != 1:
        return "no"  # absent, or ambiguous within the bar
    a = a_nodes[0]
    parents = {c: p for p in root.iter() for c in p}
    cur = a
    while cur is not None and cur.tag == "node":
        if cur.get("selected") == "true":  # AFFIRMATIVE only — never clickable=false (a label)
            return "yes"
        cur = parents.get(cur)
    return "unverified"  # A present & unique in a real bar, but no affirmative selected signal


def verify_by_affordance(pre_tap_xml: str, post_tap_xml: str, affordance_value: str,
                         focus_pkg: Optional[str], target_pkg: Optional[str]) -> str:
    """Post-TAP confirmation (stricter than current_section): everything current_section
    requires PLUS the section CONTENT changed from the pre-tap dump — a real navigation, not a
    sticky drawer staying open / an optimistic indicator flip. Returns 'arrived'|'unverified'|'no'."""
    sec = current_section(post_tap_xml, affordance_value, focus_pkg, target_pkg)
    if sec == "no":
        return "no"
    members = nav_container_members(post_tap_xml)
    if _content_digest(pre_tap_xml, members) == _content_digest(post_tap_xml, members):
        return "no"  # nothing navigated — drawer still open / optimistic selection
    return "arrived" if sec == "yes" else "unverified"
