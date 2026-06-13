"""Turn a recorded Graph into a linear command list — NO pathfinding, NO <from>/<to>.

The whole recording is replayed in chronological order (graph.ordered_transitions()): for each
recorded transition, its pre_actions (the text typed / boxes checked) then its navigating action.
Launch is handled separately by the engine via the app's anchor; the flow starts at the screen
that anchor lands on, so the launcher→app icon-tap is skipped (we cold-launch by command).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from wendle.fingerprint.signature import is_launcher_namespace
from wendle.models import Action, ForceAction


@dataclass
class Command:
    action: Optional[Action]   # None for a 'launch' command
    kind: str                  # 'pre' | 'action' | 'launch'
    edge_index: int            # chronological index of the transition this came from
    anchor: Optional[ForceAction] = None  # for a 'launch' command: the app to cold-launch


def launch_anchor(graph) -> Optional[ForceAction]:
    """The app-launch ForceAction: the first verified `am_start` anchor among the recorded
    screens (cold-launch the app by command), else the first transition's source anchor
    (e.g. a launcher keyevent for a recording that begins on the home screen)."""
    for (u, v, _k, _d) in graph.ordered_transitions():
        for sid in (u, v):
            s = graph.screen(sid)
            if s and s.force_action and s.force_action.verified and s.force_action.kind == "am_start":
                return s.force_action
    order = list(graph.ordered_transitions())
    if order:
        s = graph.screen(order[0][0])
        if s and s.force_action and s.force_action.verified:
            return s.force_action
    return None


def launch_tap(graph, anchor=None):
    """The recorded launcher ICON TAP that opened the app — the faithful way to reach a
    launcher entry that a command launch CANNOT: a launcher entry living inside a SHARED
    package (e.g. Gemini's `content_desc='Gemini'` icon lives in the Google app's package, so
    `am start` / a package launch open Google Search instead). Returns (Action, launcher
    ForceAction), or None if the recording didn't start from the launcher. The icon is matched
    by its STABLE label (content-desc/text), not coordinates.

    `anchor` parameterizes WHICH app's icon: return the launcher→app edge whose TARGET screen's
    namespace matches `anchor.value` (the icon that opens THIS app). Without it, the FIRST
    launcher edge in the graph is returned — wrong for a multi-app recording, where app B's
    launch would otherwise tap app A's icon (Gemini for Keep)."""
    want = str(anchor.value) if anchor is not None else None
    for (u, v, _k, d) in graph.ordered_transitions():
        src = graph.screen(u)
        if src is None or not is_launcher_namespace(src.namespace):
            continue
        if want is not None:
            tgt = graph.screen(v)
            if tgt is None or tgt.namespace != want:
                continue  # a launcher edge, but it opens a DIFFERENT app
        return d["action"], src.force_action
    return None


def _launch_anchor_of(screen) -> Optional[ForceAction]:
    fa = screen.force_action if screen is not None else None
    return fa if (fa is not None and fa.verified and fa.kind == "am_start") else None


def _find_anchor_for_package(graph, pkg: str) -> Optional[ForceAction]:
    """Any verified am_start anchor of `pkg` recorded ANYWHERE in the graph. Used to re-launch an
    app on RE-ENTRY (A->B->A) when the re-entry screen itself carries no anchor — so we cold-launch
    the app by command instead of replaying the fragile recorded back-gesture that got us there."""
    for sid in graph.g.nodes:
        s = graph.screen(sid)
        if (s and s.package == pkg and s.force_action
                and s.force_action.verified and s.force_action.kind == "am_start"):
            return s.force_action
    return None


def flow_from_recording(graph, start_id: Optional[str] = None) -> List[Command]:
    """Build the command list in chronological order, APP-AWARE (the multi-app finding).

    Each time the flow ENTERS an app — the foreground package changes to an app that carries a
    launch anchor — emit a LAUNCH command for that app instead of replaying the recorded launcher
    gesture that opened it (tap the home search bar, type the app name, tap the icon). Those
    gestures are fragile (a search-bar tap binds to launcher chrome) and the typing often isn't
    captured at all, so reproducing them is exactly what failed on-device; a fresh `am_start`
    is robust and is what the engine already does for the FIRST app. Launcher 'return home'
    transitions are dropped (the next app's launch supersedes them). In-app transitions replay
    normally. The first app is launched by the engine from `start_id`'s screen, so `current_pkg`
    starts as that app and it is not re-launched. An app entry with NO anchor (e.g. a share-sheet
    hop) falls back to replaying the recorded action — best effort, may stop honestly."""
    started = start_id is None
    cmds: List[Command] = []
    start_screen = graph.screen(start_id) if start_id else None
    current_pkg = start_screen.package if start_screen is not None else None
    last_v = start_id  # contiguity cursor: the previous emitted edge's target
    for ei, (u, v, _k, data) in enumerate(graph.ordered_transitions()):
        if not started:
            if u == start_id:
                started = True  # an action FROM the start screen — replay it below
            elif v == start_id:
                # the trace ENTERED the start screen here (its launch edge — superseded by
                # the engine's cold launch). Replay resumes AFTER it. Without this arm, a
                # recording whose trace leaves the anchor via an EDGE-LESS same-screen hop
                # (a chrome-forked reveal scroll — S23-confirmed OEM default) never shows
                # the anchor as a SOURCE, and the whole capture refused as flow_empty.
                started = True
                continue
            else:
                continue
        # Cap 1: a `scroll`-class chrome-fork continuation edge is NEVER a replay command — the
        # per-step reveal rung bridges the gap. Skip it for EMISSION only, AFTER the started /
        # current_pkg bookkeeping above (so a fork hop still flips `started`), preserving the
        # enumerate index the engine binds `edge_index` to.
        if data.get("action_class") == "scroll":
            last_v = v  # the fork hop DID move the device (the reveal rung bridges it at
            continue    # replay) — the contiguity cursor advances, no spurious re-anchor
        tgt = graph.screen(v)
        if tgt is not None and is_launcher_namespace(tgt.namespace):
            current_pkg = None  # back on the launcher — drop the gesture; a later launch supersedes it
            continue
        tgt_pkg = tgt.package if tgt is not None else None
        launched_here = False
        if tgt_pkg is not None and tgt_pkg != current_pkg:
            # this screen's own anchor, else ANY anchor of the same package (re-entry: the screen we
            # came back to may not be the one we first launched from)
            anchor = _launch_anchor_of(tgt) or _find_anchor_for_package(graph, tgt_pkg)
            if anchor is not None:
                cmds.append(Command(action=None, kind="launch", edge_index=ei, anchor=anchor))
                current_pkg = tgt_pkg
                launched_here = True
                src = graph.screen(u)
                if not (src is not None and src.package == tgt_pkg):
                    # a true app-ENTRY gesture (cross-package SOURCE): the fragile open tap is
                    # superseded by the cold launch — drop it
                    continue
                # NON-CONTIGUOUS trace (a crawl-star / human BACK retreat left no edge): the
                # cursor drifted to a foreign package but THIS edge is an ordinary in-app tap
                # from an in-package screen — the launch above re-anchors, and the tap itself
                # must still replay (dropping it silently lost a recorded step)
        if not launched_here and last_v is not None and u != last_v:
            # CONTIGUITY break (a crawl-star sibling / an untracked BACK): the previous
            # emitted edge left the device on `last_v`, but THIS edge departs `u`. Re-anchor
            # (the edge source's own anchor, else its package anchor) so the tap fires from a
            # deterministic screen — INDEPENDENT of the entry branch above, so an edge into an
            # anchorless foreign target still repositions at its in-package SOURCE first.
            # Without any anchor, fall through best-effort (the engine's per-step presence
            # wait + reveal rung stop honestly if the element is absent). A contiguous human
            # trace (u == last_v always) never triggers this. Exception: before the FIRST
            # command when the anchor IS the start anchor — the engine's own cold launch
            # already repositioned exactly there (the edge-less legacy fork-hop shape).
            src = graph.screen(u)
            anchor = (_launch_anchor_of(src)
                      or _find_anchor_for_package(graph, src.package if src else None))
            if anchor is not None and not (not cmds and anchor.verified_fp == start_id):
                cmds.append(Command(action=None, kind="launch", edge_index=ei, anchor=anchor))
        # in-app transition (or an app entry with no anchor) -> replay pre_actions + the action
        for pre in data.get("pre_actions", []):
            cmds.append(Command(action=pre, kind="pre", edge_index=ei))
        cmds.append(Command(action=data["action"], kind="action", edge_index=ei))
        if tgt_pkg is not None:
            current_pkg = tgt_pkg
        last_v = v
    return cmds
