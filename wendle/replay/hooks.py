"""Inter-step injection hooks — the developer's extension point during replay.

A hook is any Python CALLABLE registered to run AT a verified recorded step/screen while the
recording replays. Inside it the developer runs ARBITRARY code (a Frida subprocess, an AI agent
that controls ONE screen, a custom payload / data-extraction) reaching the device ONLY through the
framework DeviceDriver seam (`ctx.driver`), so a hook is unit-testable against FakeDriver exactly
like the engine and navigator. The navigation BETWEEN hooks is the recorded replay; the hook plugs
into the verified gap.

The hook RETURNS a directive that steers the replay — the author's shape lives INSIDE the callable:

    def at_paywall(ctx):
        state = my_agent.inspect(ctx)        # arbitrary code in the verified gap
        if state == "a":  return goto("PremiumHome")   # branch to screen X
        if state == "b":  return goto("FreeTierHome")  # branch to screen Y
        return stop("paywall_undecided")               # else: honest stop

The engine NEVER inspects WHY a hook chose a branch — it only switches on `result.kind` (mirroring
ActionResult.reason / NavOutcome.status: typed control flow, never a substring match). A hook that
returns None means cont() (the common "did my side-effect, keep replaying" case).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class HookResult:
    """A directive a hook returns. `frozen` so a hook can't smuggle state through it; construct via
    cont()/goto()/stop(). `kind` drives the engine's control flow; never substring-matched."""

    kind: str                       # "cont" | "goto" | "stop"
    node_id: Optional[str] = None   # target graph node id, for kind == "goto"
    reason: Optional[str] = None    # VALUE-FREE label, for kind == "stop" (never a secret literal)


def cont() -> HookResult:
    """Continue the linear recorded replay at the next command (the default; None == cont())."""
    return HookResult("cont")


def goto(node_id: str) -> HookResult:
    """Navigate (pathfind + verify) to the named graph node, then resume the recording from there.
    An unreachable / off-graph / non-unique target surfaces a typed HONEST stop, never a wrong land."""
    return HookResult("goto", node_id=node_id)


def stop(reason: str) -> HookResult:
    """Halt the replay HONESTLY with a value-free label (the hook author's branch decision)."""
    return HookResult("stop", reason=reason)


@dataclass
class HookContext:
    """The hook's whole world. Everything reaches the device through `driver` (the DeviceDriver
    seam), so a hook stays device-free testable. `emit(k, v)` returns values out through
    ReplayResult.data (VALUE-FREE: never emit a secret literal)."""

    driver: object                  # the DeviceDriver seam (FakeDriver in tests)
    node_id: Optional[str]          # resolved graph node we are AT — None if off-graph OR the
    #                                 resolution is AMBIGUOUS (structure twins): always None-check
    #                                 before dereferencing ctx.screen()
    namespace: str                  # observed foreground namespace
    focus_pkg: Optional[str]        # observed focused package
    step_index: int                 # index in the ORIGINAL recording's linear flow — stable
    #                                 across goto branches (never rebased to a sub-flow)
    phase: str                      # "before" | "after" (relative to the command at step_index)
    params: dict                    # the engine's redacted param map
    graph: object                   # the map — so a hook can validate a goto target / inspect screens
    _data: dict = field(default_factory=dict)  # accumulates into ReplayResult.data

    def emit(self, key: str, value) -> None:
        """Surface a value out of the run via ReplayResult.data (value-free — never a secret)."""
        self._data[key] = value

    def find(self, selector) -> bool:
        """Cheap presence probe through the driver seam."""
        return self.driver.xpath_exists(selector)

    def screen(self):
        return self.graph.screen(self.node_id) if self.node_id else None


Hook = Callable[["HookContext"], Optional[HookResult]]


class HookRegistry:
    """Decorator-style hook registration — the idiomatic Python pattern (Flask/FastAPI/pytest).
    Pure sugar over the same ``{key: [callable]}`` dict the engine fires from (and ``Graph.hooks``
    stores), so ``engine.run(hooks=registry)`` and ``engine.run(hooks={...})`` are interchangeable.

        hooks = HookRegistry()

        @hooks.screen("com.bank/.AccountActivity")   # fires when that screen is reached
        def grab_balance(ctx):
            ctx.emit("balance", read_balance_via_frida(ctx))
            return cont()

        @hooks.after(3)                              # fires right after recorded step 3
        def branch(ctx):
            return goto("Checkout") if ok(ctx) else stop("declined")

        engine.run(hooks=hooks)
    """

    def __init__(self) -> None:
        self.hooks: Dict[str, List[Hook]] = {}

    def on(self, key: str) -> Callable[[Hook], Hook]:
        """Register under a RAW key: ``"screen:<namespace>"`` | ``"before:<n>"`` | ``"after:<n>"``."""
        def deco(fn: Hook) -> Hook:
            self.hooks.setdefault(key, []).append(fn)
            return fn  # return the original so the function stays callable/stackable
        return deco

    def screen(self, namespace: str) -> Callable[[Hook], Hook]:
        """Fire when the replay ARRIVES at the screen with this foreground namespace —
        EDGE-triggered: once per contiguous visit, re-armed by leaving and returning. Notes:
        * On a single-Activity app every logical screen shares the namespace, so a namespace
          key fires once per namespace CHANGE; key on the node id (``on("screen:<node_id>")``)
          for per-screen firing — which fires only when the node resolves unambiguously
          (structure twins honestly fire nothing rather than guess).
        * Screen hooks fire at replay boundaries only: the entry screen before step 0, any
          other screen at the boundary after the command that reached it. Screens traversed
          inside a goto's navigation are not boundaries; a directive returned earlier at the
          same boundary consumes the arrival.
        * A foreground flicker (dialog/sub-activity) counts as leave + re-enter."""
        return self.on("screen:" + namespace)

    def before(self, step_index: int) -> Callable[[Hook], Hook]:
        """Fire immediately BEFORE the command at this step index runs."""
        return self.on(f"before:{step_index}")

    def after(self, step_index: int) -> Callable[[Hook], Hook]:
        """Fire immediately AFTER the command at this step index verifies."""
        return self.on(f"after:{step_index}")

    def asdict(self) -> Dict[str, List[Hook]]:
        return self.hooks
