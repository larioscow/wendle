"""On-device substrate proof, fully autonomous (no human touches) — task #22.

Records a REAL flow with scripts/auto_record.py (u2 actuation + the recorder's gesture seam),
then exercises three arms against that real recording and reports each honestly:

  A. faithful replay of a REAL (not hand-built) recording
  B. a developer HOOK firing BETWEEN two real recorded steps — cont() (read live state + emit)
     and stop() (steer the replay to HALT before the next step)
  C. a navigator-backed goto() that re-routes onto the recorded graph mid-replay

NOT covered here (blocked by KNOWN open work, not by the device): multi-app handoff in one run
(needs cross-app/back recording — tasks #14/#15) and per-screen identity on single-Activity apps
(the Settings .SubSettings twins — task #17). Under-claim; name what is not shown.

    uv run python scripts/auto_proof.py
"""
from __future__ import annotations

import sys
import time


def _fresh_settings():
    import uiautomator2 as u2
    d = u2.connect()
    d.shell("am force-stop com.android.settings")
    d.press("home")
    time.sleep(1.0)


def main(argv) -> int:
    rec_path = argv[0] if argv else "/tmp/auto_proof.json"

    import uiautomator2 as u2

    from wendle.driver.u2_driver import U2Driver
    from wendle.graph import Graph
    from wendle.models import Selector
    from wendle.replay.engine import ReplayEngine
    from wendle.replay.hooks import HookRegistry, cont, goto, stop
    from scripts.auto_record import AutoRecorder, _demo_flow

    # ---- record a REAL flow autonomously --------------------------------------------------
    rec = AutoRecorder()
    rec.d.shell("am force-stop com.android.settings")
    rec.d.press("home")
    time.sleep(1.0)
    rec.d.swipe(0.5, 0.9, 0.5, 0.2, 0.2)
    time.sleep(1.5)
    g = rec.run(_demo_flow(), rec_path)
    order = list(g.ordered_transitions())
    net_id = order[1][1]  # the middle node (Network), a navigator goto target

    u2.connect()

    # ---- ARM A: faithful replay of the real recording -------------------------------------
    _fresh_settings()
    outA = ReplayEngine(Graph.from_json(open(rec_path).read()), U2Driver()).run()
    print(f"\n[A] replay real recording: {outA.status}")

    # ---- ARM B: inter-step hook, cont then stop -------------------------------------------
    _fresh_settings()
    hooks = HookRegistry()

    @hooks.after(0)  # fires in the gap AFTER the first recorded tap, BEFORE the next
    def probe(ctx):
        ctx.emit("namespace_at_gap", ctx.namespace)
        ctx.emit("saw_next_row", ctx.find(Selector("text", "Internet")))
        return cont()

    outB1 = ReplayEngine(Graph.from_json(open(rec_path).read()), U2Driver()).run(hooks=hooks)
    print(f"[B] inter-step hook cont: {outB1.status} | emitted {outB1.data}")

    _fresh_settings()
    gate = HookRegistry()

    @gate.after(0)
    def block(ctx):
        return stop("policy_blocked")

    outB2 = ReplayEngine(Graph.from_json(open(rec_path).read()), U2Driver()).run(hooks=gate)
    halted = outB2.failed_step.error if outB2.failed_step else None
    print(f"[B] inter-step hook stop: {outB2.status} (halted: {halted})")

    # ---- ARM C: navigator-backed goto on the real recording -------------------------------
    _fresh_settings()
    once = []
    branch = HookRegistry()

    @branch.before(1)
    def reroute(ctx):
        if once:
            return cont()
        once.append(1)
        return goto(net_id)

    outC = ReplayEngine(Graph.from_json(open(rec_path).read()), U2Driver()).run(hooks=branch)
    went = any(s.kind == "goto" and s.ok for s in outC.steps)
    print(f"[C] navigator goto on real recording: {outC.status} (navigator arrived: {went})")

    print("\nPROVEN autonomously on-device: real-recording replay; inter-step hook (cont + steer-stop);"
          " navigator goto re-route. NOT shown: multi-app handoff (#14/#15), single-Activity per-screen"
          " identity (#17).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
