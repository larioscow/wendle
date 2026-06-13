"""The launch-strategy ladder — ONE ordered list of small pluggable units behind ONE shared
verify_foreground gate: the ONE launch path for both the replay engine and the navigator
(which share a single instance per engine, gate and winning-rung cache included). Adding a
launch case = a new ~6-line strategy + its test; it physically cannot bloat the others, and
the honesty gate lives in ONE place.

Grounded in the Android launch taxonomy (Maestro `launchApp` / Appium `appWaitActivity` /
`am start -n` vs `monkey -c LAUNCHER`): you START whatever you can launch and WAIT for the
recorded surface to foreground. A launch is "landed" ONLY when the gate confirms the recorded
namespace foregrounds — NEVER on an exit code (am start / monkey exit codes lie: a Permission
Denial still prints "Starting…", a package-default launch returns success while landing the
WRONG surface — Google Search instead of Gemini's robin activity).

Taxonomy (ordering_rank): the am_start ladder tries, in order,
  1. RecordedComponent  `am start -n pkg/activity`  — wins for an EXPORTED standalone entry
  2. IconTap            reproduce the recorded launcher icon tap — the only reach for a
                        launcher entry inside a SHARED package (Gemini in the Google app)
  3. PackageDefault     `am start` the resolved launcher entry — a non-exported splash that
                        ROUTES ITSELF to the recorded screen (BanCoppel)
A launcher-keyevent anchor (a recording that began on the OEM home screen) is a MUTUALLY
EXCLUSIVE branch — HomePress — NOT a rung of the am_start ladder: it intentionally lands on the
launcher (which the gate would reject), so readiness is handed to the caller's next step (its
element-wait is the gate, Maestro-style).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from wendle.fingerprint.signature import is_launcher_namespace


@dataclass
class LaunchResult:
    """Outcome of running the ladder for one anchor. `deferred` = readiness was handed off to
    the caller's next step (HomePress), so `landed` is True WITHOUT a namespace gate.
    `observation` = the gate's PASSING (xml, ns, focus, settled) tuple on a gated landing —
    callers consume it instead of immediately re-observing the screen the gate just verified."""

    landed: bool
    error: Optional[str] = None
    deferred: bool = False
    observation: Optional[tuple] = None


def _coerce_key_code(value) -> Optional[int]:
    """A launch key code coerced HONESTLY: an uncoercible value yields None (an honest not-landed
    at the call site), never a propagating crash. Mirrors replay.actions._keyevent's guard so a
    malformed / legacy / cross-tool-ingested anchor is a typed not-landed, never a ValueError that
    escapes engine.run()/navigate (honesty-first #1). App-agnostic: a launch anchor with an
    uncoercible key code is not-landed, the same rule the keyevent ACTION already enforces."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---- the rungs. each ISSUES its launch action and returns the gate WINDOW to wait, or None to
#      skip (not applicable). a rung that raises (a non-exported component refused by am start)
#      is caught at the LADDER level and treated as a skip — the gate is the sole arbiter. ----

def _recorded_component(ladder, anchor) -> Optional[float]:
    if getattr(anchor, "provenance", "unknown") == "self_routing":
        return None  # recorder DEFERRED this anchor past a splash -> `am start -n` is doomed; skip
    pkg, _, activity = anchor.value.partition("/")
    if not activity:
        return None
    ladder.driver.app_start(pkg, activity, stop=True)
    return ladder.activity_launch_timeout  # SHORT: a refused/non-exported component fails fast


def _icon_tap(ladder, anchor) -> Optional[float]:
    # lazy import keeps wendle.launch free of the replay package (whose __init__ pulls
    # in the engine -> navigator); the import runs at call time, after modules are loaded.
    from wendle.replay.commands import launch_tap

    lt = launch_tap(ladder.graph, anchor)
    if lt is None:
        return None
    tap, home = lt
    if home is not None:
        home_code = _coerce_key_code(home.value)
        if home_code is None:
            return None  # malformed home anchor -> rung not applicable; the ladder advances
        ladder.driver.keyevent(home_code)  # go HOME first, then tap the icon by its label
    # The icon may not be on the home PAGE that HOME lands on (multi-page launcher / app drawer).
    # Wait for it and SKIP the rung (return None) when it never appears, rather than firing
    # resolve_and_tap at an absent icon and falling through to a wrong app. (icon-REACH residual.)
    if not ladder.driver.wait_until_present(tap.selector, timeout=1.0,
                                            clock=ladder.clock, sleep=ladder.sleep):
        return None
    ladder.driver.resolve_and_tap(tap.selector, tap.action_type)
    return ladder.launch_timeout


def _package_default(ladder, anchor) -> Optional[float]:
    pkg = anchor.value.partition("/")[0]
    ladder.driver.app_start(pkg, None, stop=True)
    return ladder.launch_timeout


def _monkey_launcher(ladder, anchor) -> Optional[float]:
    # Last-ditch rescue: a non-exported SINGLE-entry package whose `am start -n mainActivity`
    # (package_default) is refused but whose MAIN/LAUNCHER intent resolves via monkey. For a
    # MULTI-entry package monkey lands the package DEFAULT (device-confirmed: Gemini -> Google
    # Search), so the gate rejects it and the ladder honestly exhausts — never a wrong replay.
    if not ladder.driver.supports("launch_monkey"):
        return None
    ladder.driver.launch_monkey(anchor.value.partition("/")[0])
    return ladder.launch_timeout


# ordered list of (name, rung, resolution mechanism). New cases plug in here; they cannot touch
# the others or the gate. The MECHANISM tags how the rung resolves "which surface opens":
#   component       — an explicit `am start -n pkg/activity`
#   gesture         — reproducing the recorded human launcher gesture
#   intent_default  — the system's MAIN/LAUNCHER default resolution (am start pkg, monkey both
#                     resolve the SAME entry, so one wrong landing condemns the other)
AM_START_LADDER = (
    ("recorded_component", _recorded_component, "component"),
    ("icon_tap", _icon_tap, "gesture"),
    ("package_default", _package_default, "intent_default"),
    ("monkey_launcher", _monkey_launcher, "intent_default"),
)


class LaunchLadder:
    """Run the launch ladder for an anchor behind ONE verify_foreground gate.

    `observe` is a zero-arg callable returning (xml, namespace, focus_pkg, settled) — the SAME
    settle-gated observation both the engine and navigator already use. clock/sleep are injectable
    so device-free tests run at ~0 wall-time and no wait is ever a blind sleep.
    """

    def __init__(self, graph, driver, observe, *, clock=time.monotonic, sleep=time.sleep,
                 activity_launch_timeout: float = 4.0, launch_timeout: float = 15.0):
        self.graph = graph
        self.driver = driver
        self.observe = observe
        self.clock = clock
        self.sleep = sleep
        self.activity_launch_timeout = activity_launch_timeout
        self.launch_timeout = launch_timeout
        # winning-rung cache: anchor identity -> the rung name that LANDED it. A re-launch of
        # the same anchor tries the proven rung FIRST, so a restart never re-thrashes through
        # refused rungs (whose stop=True force-stops kill shared-package state — Search+Gemini).
        self._winning: dict = {}

    def _verify_foreground(self, anchor, deadline: float) -> bool:
        """THE launch gate (Maestro-style `launchApp` + per-command waits). Poll until the
        foreground namespace IS the recorded pkg/activity (and not the launcher), else the deadline
        passes. FULL-namespace match (pkg/activity, NOT package-only) is what both drives the ladder
        and closes the wrong-APP trap: a shared-package launch landing the package DEFAULT (Google
        Search, not Gemini's robin activity) has a DIFFERENT activity -> rejected -> the ladder
        advances to the icon tap.

        Screen CONTENT identity is deliberately NOT gated here. App homes are frequently dynamic
        (Settings suggestion cards, feeds, greetings) and the recorder may hold BOTH a volatile and
        a settled node for one screen, so a fingerprint/structure-identity gate OVER-REJECTS correct
        launches — it made a plain Settings replay re-open the app on every rung until the ladder
        gave up (real-device ground truth). Exactly as Maestro: the launch establishes the
        app+activity, and the FLOW's first command then waits for its recorded element and STOPS
        HONESTLY if we are on the wrong screen. (The navigator adds its own verify_match arrival
        check on top — its closed loop IS its flow backstop.) Accepted residual, same as Maestro: a wrong
        same-activity screen that happens to show the recorded next element is not caught — rare.

        No blind sleeps: the poll exits the instant the namespace holds; clock/sleep are injectable.

        Returns the PASSING observation tuple (so the caller can consume it instead of
        re-observing the screen the gate just verified), or None when the deadline passes."""
        anchor_value = str(anchor.value)
        while True:
            obs = self.observe()
            _x, ns, _f, _s = obs
            if str(ns) == anchor_value and not is_launcher_namespace(ns):
                return obs
            if self.clock() >= deadline:
                return None
            self.sleep(0.3)

    def launch(self, anchor) -> LaunchResult:
        if anchor is None:
            return LaunchResult(landed=False, error="no launch anchor")
        if anchor.kind != "am_start":
            # HomePress — exclusive branch. Press the key (keyevent 3 -> launcher home) and hand
            # readiness to the caller's next step; NO namespace gate (we deliberately land on the
            # launcher, which the gate rejects). The next command's element-wait is the gate.
            code = _coerce_key_code(anchor.value)
            if code is None:  # corrupted / legacy / cross-tool anchor -> honest not-landed, never a crash
                return LaunchResult(landed=False, error="malformed keyevent anchor")
            self.driver.keyevent(code)
            return LaunchResult(landed=True, deferred=True)
        key = anchor.verified_fp or f"{anchor.kind}:{anchor.value}"
        cached = self._winning.get(key)
        order = AM_START_LADDER if cached is None else \
            tuple(sorted(AM_START_LADDER, key=lambda r: r[0] != cached))  # proven rung first;
        #   sorted-by-bool is stable, so the remaining rungs keep their taxonomy order (full
        #   ladder fallback when the proven rung stops landing — launcher reshuffle etc.)
        wrong_mechanisms: set = set()
        for name, rung, mechanism in order:
            if mechanism in wrong_mechanisms:
                continue  # this resolution mechanism already landed a wrong surface — re-running
                #           it (monkey after package_default) can only re-open the same wrong app
            try:
                window = rung(self, anchor)
            except Exception:  # noqa: BLE001 — a refused/raising rung is NOT_LANDED; advance
                window = None
            if window is None:
                continue  # not applicable (no activity / no recorded icon) — next rung
            obs = self._verify_foreground(anchor, self.clock() + window)
            if obs is not None:
                self._winning[key] = name
                return LaunchResult(landed=True, observation=obs)
            # ANTI-THRASH, MECHANISM-SCOPED (review finding 11): the gate rejected this rung. A
            # REAL but WRONG surface (a different, non-launcher namespace) condemns only the rungs
            # that resolve the SAME way — intent-default resolution re-lands the same default
            # entry (Google Search instead of Gemini, device-confirmed), but a DIFFERENT mechanism
            # (the recorded icon gesture after a self-routing `am start -n`) may still reproduce
            # the recorded entry. A landing back on the launcher (nothing foregrounded) is NOT a
            # wrong surface — advance. KNOWN ACCEPTED RESIDUAL (adversarial review, LOW): the
            # observation can be a PREVIOUS rung's residue rather than this rung's product, so
            # under the cache's reorder a silently-no-op rung can be condemned for a surface it
            # never opened (rare; honest exhaustion, and pop-on-exhaust self-heals the next
            # launch). Post-hoc namespace polling cannot distinguish "still there" from
            # "re-landed", and exempting residue would un-condemn the device-confirmed
            # intent-default re-landing (Gemini -> Google Search) — the worse trade.
            _x, ns, _f, _s = self.observe()
            if str(ns) != str(anchor.value) and not is_launcher_namespace(ns):
                wrong_mechanisms.add(mechanism)
        self._winning.pop(key, None)  # exhausted: don't keep steering relaunches to a dead rung
        return LaunchResult(landed=False,
                            error="wrong_surface" if wrong_mechanisms else "app did not foreground")
