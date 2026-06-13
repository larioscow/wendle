"""Smoke-test of the inter-step injection substrate on a Frida-capable device.

The product-defining loop: faithful replay is the SUBSTRATE; the developer injects code
(here, Frida) in the GAP between replay steps, and the injected result can STEER the replay
(cont / honest stop / goto).

What THIS demo proves on-device (single app, hand-built 2-node graph, one hook):
  1. ReplayEngine launches Settings via the LaunchLadder (the same path proven on 50 apps).
  2. BEFORE the first step, a developer HOOK attaches Frida to the live, foregrounded
     Settings process, reads a real in-process value (loaded native module count), EMITS
     it value-free onto ReplayResult.data, and returns a TYPED directive.
  3. Contingency (run it twice, alive vs dead DEMO_FRIDA_HOST): a reachable endpoint -> cont()
     -> completed; a dead endpoint -> stop('frida_attach_failed') -> the replay HALTS before
     the next step. The injected result alone flips the replay verdict — no fake success.

What it does NOT prove (tracked, not claimed): a goto() branch onto a different path, multi-app
handoff in one run, AI-agent (non-Frida) injection, or replay of a REAL recording (the graph
here is hand-built). Those are the next on-device milestones.

Run on a CLEAN throwaway AVD (NEVER the bancoppel-kyc emulator — see the project memory):
  1. adb -s <serial> root && adb -s <serial> shell setenforce 0
  2. push a frida-server matching `frida --version`, then: frida-server -l 127.0.0.1:<PORT>
  3. adb -s <serial> forward tcp:<PORT> tcp:<PORT>
  4. DEMO_SERIAL=<serial> DEMO_FRIDA_HOST=127.0.0.1:<PORT> \
       uv run python scripts/demo_hook_frida.py
"""
from __future__ import annotations

import os
import re
import subprocess

from wendle.driver.u2_driver import U2Driver
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.replay.engine import ReplayEngine
from wendle.replay.hooks import HookRegistry, cont, stop

SERIAL = os.environ.get("DEMO_SERIAL")  # None -> u2 picks the only connected device
FRIDA_HOST = os.environ.get("DEMO_FRIDA_HOST", "127.0.0.1:27045")
PKG = "com.android.settings"
ACTIVITY = ".Settings"
KEYCODE_DPAD_DOWN = "20"  # an inert step — its only job is to create a boundary for the hook


def build_graph() -> Graph:
    """A tiny hand-built recording: launch Settings, then one inert step (so there is a
    verified boundary for the hook to fire at). Stands in for a real on-device recording."""
    g = Graph()
    ns = f"{PKG}/{ACTIVITY}"
    g.upsert_screen(Screen(id="S0", namespace=ns, package=PKG, activity=ACTIVITY,
                           force_action=ForceAction("am_start", ns, verified_fp="S0",
                                                     provenance="launcher_entry")))
    g.upsert_screen(Screen(id="S1", namespace=ns, package=PKG, activity=ACTIVITY))
    step = Action(selector=Selector("keyevent", KEYCODE_DPAD_DOWN), action_type="keyevent")
    g.add_transition(Transition(source="S0", target="S1", action=step))
    return g


def frida_probe(ctx):
    """Developer-injected code: runs in the gap AFTER replay reaches Settings, BEFORE the
    next step. Attaches Frida to the live app, reads a real value, emits it, and steers."""
    pid = (ctx.driver.shell(f"pidof {PKG}") or "").strip()
    if not pid.isdigit():
        return stop("target_not_running")
    js = 'console.log("PROBE mods=" + Process.enumerateModules().length + " arch=" + Process.arch)'
    try:
        p = subprocess.run(["frida", "-H", FRIDA_HOST, "-p", pid, "-q", "-e", js],
                           capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return stop("frida_unavailable")
    m = re.search(r"PROBE mods=(\d+) arch=(\S+)", p.stdout + p.stderr)
    if not m:
        return stop("frida_attach_failed")  # honest: the inject didn't run -> don't barrel on
    mods, arch = int(m.group(1)), m.group(2)
    ctx.emit("frida_pid", int(pid))
    # cross-check: the PID we attached to belongs to the app the ENGINE verified as foreground
    # (ctx.focus_pkg comes from the engine's own _observe), not just "a process named PKG".
    ctx.emit("frida_pid_is_foreground", ctx.focus_pkg == PKG)
    ctx.emit("frida_modules", mods)
    ctx.emit("frida_arch", arch)
    # BRANCH on the injected result. NOTE: mods > 50 is only a liveness floor (every live app maps
    # hundreds), so on a reachable endpoint this almost always takes cont(); the MEANINGFUL
    # contingency this demo shows is attach-success vs failure — run with a dead DEMO_FRIDA_HOST to
    # take the honest-stop arm (frida_attach_failed) and watch the replay halt before the next step.
    return cont() if mods > 50 else stop("frida_underinstrumented")


def main() -> None:
    driver = U2Driver(serial=SERIAL)
    hooks = HookRegistry()
    hooks.before(0)(frida_probe)  # inject right after launch, before the first step
    print(f"[demo] frida endpoint = {FRIDA_HOST}; launching {PKG}, then firing the hook")
    out = ReplayEngine(build_graph(), driver).run(
        hooks=hooks,
        on_step=lambda s: print(f"  step{s.index:>2} {s.kind}/{s.action_type} ok={s.ok} {s.error or ''}"),
    )
    print(f"[demo] status = {out.status}")
    print(f"[demo] emitted = {out.data}")
    # HONEST reporting — deliberately NOT a "SUBSTRATE PROVEN" banner. A single-app, hand-built-graph
    # run proves the inject+read+emit slice and (paired with a dead-endpoint run) the honest-stop
    # contingency; it does NOT prove the whole substrate. Under-claim; name what is NOT yet shown.
    injected = out.data.get("frida_modules") is not None
    if out.status == "completed" and injected:
        print(f"[demo] EXERCISED — inject+read+emit (cont arm): a developer Frida hook attached to the "
              f"live foreground {PKG} (pid {out.data['frida_pid']}, foreground={out.data.get('frida_pid_is_foreground')}, "
              f"{out.data['frida_modules']} modules) in the gap after launch and emitted a real "
              f"in-process value. The hook returned cont(), so the replay continued UNCHANGED.")
    elif out.status == "stopped" and out.failed_step is not None:
        print(f"[demo] EXERCISED — honest-stop arm: the hook could not complete the inject and returned "
              f"stop, so the replay HALTED ({out.failed_step.error}) BEFORE the next step — no fake success.")
    print("[demo] NOT proven by this run: a goto() branch onto a different path, multi-app handoff in "
          "one run, AI-agent (non-Frida) injection, replay of a REAL recording, or a hook between two "
          "real recorded steps. Pair an alive + a dead DEMO_FRIDA_HOST run to see the steering contingency.")


if __name__ == "__main__":
    main()
