from __future__ import annotations

from dataclasses import replace

from defusedxml.ElementTree import fromstring as _xml_fromstring

from wendle.fingerprint.signature import (
    FingerprintConfig,
    _is_ime_pkg,
    _rid_parts,
    is_launcher_namespace,
)

# Exact Compose host classes — substring matching false-positives on custom
# 'MyComposeViewWrapper' / 'PreComposeView'.
COMPOSE_HOSTS = frozenset(
    {
        "androidx.compose.ui.platform.AndroidComposeView",
        "androidx.compose.ui.platform.ComposeView",
    }
)
_DECOR_HINTS = ("navigationBar", "statusBar")
_DECOR_PKGS = ("com.android.systemui",)

# Three fixed profiles (no runtime ladder — APE-style refinement is deferred to Spike 3).
VIEW_PROFILE = FingerprintConfig()
COMPOSE_PROFILE = replace(VIEW_PROFILE, include_text=True, title_value_max_depth=4)
# Launcher/home is namespace-dominant in fingerprint() (structure ignored). This
# profile is retained for symmetry; the launcher's structural signature is not used.
LAUNCHER_PROFILE = replace(VIEW_PROFILE, max_depth=2, shallow_summary=False)


def _is_host(cls: str) -> bool:
    return cls in COMPOSE_HOSTS


def _is_decor_or_ime(el) -> bool:
    if _is_ime_pkg(el.get("package", "")):
        return True
    rid = el.get("resource-id", "")
    pkg, _ = _rid_parts(rid)
    if pkg in _DECOR_PKGS:
        return True
    return any(h in rid for h in _DECOR_HINTS)


def is_compose_dominant(xml: str, threshold: float = 0.7) -> bool:
    """True when most non-decor, non-IME leaf nodes descend from a Compose host (§7).

    Excludes the host itself, system decor, AND IME leaves from the denominator —
    a soft keyboard (View-based) otherwise inflates the non-Compose leaf count and
    flips a Compose screen below threshold mid-capture.
    """
    try:
        root = _xml_fromstring(xml)
    except Exception:  # noqa: BLE001
        return False
    parents = {c: p for p in root.iter() for c in p}
    leaves = [
        el
        for el in root.iter("node")
        if not any(c.tag == "node" for c in el)
        and not _is_host(el.get("class", ""))
        and not _is_decor_or_ime(el)
    ]
    if not leaves:
        return False

    def under_compose(el) -> bool:
        cur = parents.get(el)
        while cur is not None:
            if _is_host(cur.get("class", "")):
                return True
            cur = parents.get(cur)
        return False

    n_compose = sum(1 for leaf in leaves if under_compose(leaf))
    return n_compose / len(leaves) >= threshold


def resolve_profile(settled_xml: str, namespace: str = None) -> FingerprintConfig:
    """Choose the fingerprint profile ONCE, on the settled dump — never per-dump.

    Per-dump selection let the Compose-dominance classifier flip across a screen's
    own re-dumps (verified in corpus), and since the profiles are different
    abstractions, the config choice itself manufactured instability. Deciding once
    on the settled hierarchy removes that flicker. Launcher namespaces get the
    shallow profile so swipe-pages collapse to one 'home' node.
    """
    if namespace is not None and is_launcher_namespace(namespace):
        return LAUNCHER_PROFILE
    return COMPOSE_PROFILE if is_compose_dominant(settled_xml) else VIEW_PROFILE


def select_config(xml: str) -> FingerprintConfig:
    """Deprecated alias — prefer resolve_profile on the settled dump."""
    return resolve_profile(xml)


def compose_config() -> FingerprintConfig:
    """The Compose profile (promotes text + shallow title values)."""
    return COMPOSE_PROFILE

