from __future__ import annotations

import re
from typing import Optional

# Android 12+ renamed mResumedActivity -> topResumedActivity; accept both,
# anchored on ActivityRecord{... u<id> pkg/activity ...}.
_RESUMED = re.compile(
    r"(?:m|top)?ResumedActivity\s*[:=]\s*ActivityRecord\{[^}\n]*?\bu\d+\s+([\w.]+)/([\w.$]+)"
)
_FOCUSED_ACT = re.compile(r"mFocusedActivity[^\n]*?\bu\d+\s+([\w.]+)/([\w.$]+)")
# mCurrentFocus=Window{<hash> [u0 ]name} — the user token is optional on some OEMs,
# and we never capture the hash.
_CURRENT_FOCUS = re.compile(r"mCurrentFocus=Window\{\S+\s+(?:u\d+\s+)?([^\s}]+)\}")


def parse_resumed_activity(activity_dump: str) -> Optional[str]:
    """Extract 'package/activity' from `dumpsys activity activities` (m/topResumedActivity)."""
    m = _RESUMED.search(activity_dump) or _FOCUSED_ACT.search(activity_dump)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def parse_focused_window(window_dump: str) -> Optional[str]:
    """Extract the focused window name from `dumpsys window`.

    Catches SystemUI surfaces (NotificationShade, volume dialog, keyguard) that
    are NOT resumed activities, so they get distinct namespaces (§7)."""
    m = _CURRENT_FOCUS.search(window_dump)
    return m.group(1) if m else None


def _pkg(signal: Optional[str]) -> Optional[str]:
    return signal.split("/", 1)[0] if signal and "/" in signal else None


# Focused window NAMES that are SystemUI surfaces (no package in the name).
_SYSTEMUI_WINDOWS = (
    "NotificationShade",
    "StatusBar",
    "VolumeDialog",
    "Keyguard",
    "ScreenDecor",
    "QuickSettings",
)


def focused_package(window_dump: str) -> Optional[str]:
    """Resolve the focused window to its PACKAGE, for the sanitizer's focus gate.

    'com.pkg/.Activity' -> 'com.pkg'; SystemUI window names (NotificationShade,
    volume dialog, keyguard) -> 'com.android.systemui'. Lets the sanitizer keep
    the shade/keyguard when it IS the screen, and strip it when it's an overlay.
    """
    win = parse_focused_window(window_dump)
    if not win:
        return None
    if "/" in win:
        return win.split("/", 1)[0]
    if any(name in win for name in _SYSTEMUI_WINDOWS):
        return "com.android.systemui"
    return None


def foreground_namespace(activity_dump: str, window_dump: str) -> str:
    """Coarse foreground namespace for the fingerprint (§7).

    - A non-activity system window (no '/', e.g. NotificationShade) -> its own
      namespace, even if an activity is resumed underneath.
    - Activity-style focus: trust the resumed activity only when its PACKAGE
      agrees with the focused window's; on disagreement (a permission/overlay
      activity from another package, or RecentsActivity over the launcher) return
      the focused window — what the user is actually looking at.
    - Neither parses -> 'unknown' (never crash a crawl on a namespace miss)."""
    act = parse_resumed_activity(activity_dump)
    win = parse_focused_window(window_dump)
    if win and "/" not in win:
        return win
    if act and win:
        return act if _pkg(act) == _pkg(win) else win
    return act or win or "unknown"
