"""A mini PROGRAM built on wendle, used exactly as intended.

The framework gave us (with NO human walk):
  * `sweep_settings.json` — a crawl-built map of 37 REAL, verified screen-nodes;
  * the public verbs `navigate` / `replay_recording`;
  * THE defining feature: hooks that run in the VERIFIED GAPS between steps, read live
    device state through the driver seam, and STEER the run — cont() / goto(node) /
    honest stop(reason).

The program is a tiny "Settings auditor":
  PART 1 (map-router): navigate to audit screens by NODE and read live facts off each
          verified arrival.
  PART 2 (hooked replay): replay the recorded flow, but a before-hook immediately
          GOTOs past the shallow steps to the deep (scrolled) section; screen-hooks
          then collect facts at each verified arrival and STOP honestly once the audit
          is complete — the recorded flow is the skeleton, the hooks are the brain.

    uv run python examples/settings_assistant.py
"""
from __future__ import annotations

import sys
import time

from wendle import Graph, U2Driver, navigate, replay_recording
from wendle.capture.hierarchy import parse_hierarchy
from wendle.replay.hooks import HookRegistry, goto, stop

MAP = "sweep_settings.json"
PKG = "com.android.settings"


# ---- tiny helpers a real consumer would write ----------------------------------------

def node_by_label(graph: Graph, label: str) -> str:
    """Find the screen-node an edge labeled `label` leads to (the consumer's handle onto
    the crawl-built map: recorded actions are the map's own vocabulary)."""
    for _u, v, _k, data in graph.ordered_transitions():
        if data["action"].selector.value == label:
            return v
    raise KeyError(label)


def live_facts(driver) -> dict:
    """Read VALUE-LIGHT facts from the live screen through the driver seam."""
    nodes = parse_hierarchy(driver.dump_hierarchy())
    texts = [n.text for n in nodes if n.text]
    return {
        "rows": sum(1 for n in nodes if n.clickable),
        # longest visible text reads as the screen's heading (the first text can be an
        # in-app clock preview on lock-screen settings)
        "title": max(texts, key=len) if texts else "",
        "toggles": sum(1 for n in nodes if n.checkable),
    }


def fresh_start(driver):
    driver.shell(f"am force-stop {PKG}")
    time.sleep(0.8)
    driver.keyevent(3)
    time.sleep(0.8)


def main() -> int:
    graph = Graph.from_json(open(MAP).read())
    driver = U2Driver()
    top = next(s for s in (graph.screen(n) for n in graph.g.nodes)
               if s.force_action is not None and s.package == PKG).id

    # ---- PART 1: the map-router as an assistant --------------------------------------
    print("== PART 1: navigate the crawl-built map and audit each verified arrival ==")
    for label in ("Conexiones", "Sonidos y vibración"):
        target = node_by_label(graph, label)
        fresh_start(driver)
        out = navigate(graph, top, target, driver)
        if out.status != "arrived":
            print(f"  {label:28} -> HONEST STOP: {out.status} ({out.detail})")
            continue
        facts = live_facts(driver)
        print(f"  {label:28} -> arrived; rows={facts['rows']} toggles={facts['toggles']}")

    # ---- PART 2: hooked replay — code in the verified gaps steers the run ------------
    print("\n== PART 2: replay with hooks (goto skip-ahead + live reads + honest stop) ==")
    deep_section = node_by_label(graph, "Notificaciones")  # lives on the SCROLLED twin
    scrolled_twin = next(u for u, v, _k, d in graph.ordered_transitions() if v == deep_section)
    hooks = HookRegistry()
    audit: list = []

    @hooks.before(0)
    def skip_to_deep_section(ctx):
        # Steering decision in the FIRST verified gap: we only care about the deep
        # section today — jump straight there (pathfind + verified arrival; an
        # unreachable target would be a TYPED stop, never a wrong landing).
        return goto(scrolled_twin)

    @hooks.screen(f"{PKG}/.SubSettings")
    def collect(ctx):
        # Runs at every VERIFIED SubSettings arrival. ctx.node_id is the resolved map
        # node (None when ambiguous — always check); the driver seam reads live state.
        facts = live_facts(ctx.driver)
        audit.append(facts["title"])
        ctx.emit(f"audit_{len(audit)}", f"{facts['title']!r} rows={facts['rows']}")
        if len(audit) >= 2:
            return stop("audit_complete")  # honest early exit — the hook's branch decision
        return None  # cont(): let the recorded skeleton carry us to the next screen

    fresh_start(driver)
    result = replay_recording(MAP, driver, hooks=hooks)
    print(f"  replay: {result.status}"
          + (f" ({result.stop_reason.kind}: {result.failed_step.error})"
             if result.status == 'stopped' and result.failed_step else ""))
    for k, v in (result.data or {}).items():
        print(f"    {k}: {v}")
    ok = result.status == "stopped" and "audit_complete" in (result.failed_step.error or "")
    print(f"\n[assistant] audited {len(audit)} deep screens via goto+hooks: "
          f"{'SUCCESS' if ok and audit else 'see above'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
