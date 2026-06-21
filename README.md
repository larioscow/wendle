# wendle

wendle is a Python framework for black-box Android UI automation, built on ADB and
`uiautomator2`. It records a manual walkthrough of a device into a directed graph of
screens that spans multiple apps and system surfaces (Settings, the launcher, the
notification shade), not a single app, then replays that recording and navigates between
any two screens on the graph. Arrivals are verified by screen fingerprint; on a mismatch
it stops and reports rather than guessing. Hooks can run arbitrary code (Frida, an LLM,
plain Python) between replayed steps.

## CLI

Everything runs from the command line against one `phone.json`. You don't write Python until
you want custom logic.

```bash
wendle record --out phone.json --duration 90   # walk the device by hand; save the map
wendle replay phone.json --param password=…     # re-enact it (credentials never logged)
wendle replay phone.json --hooks my_hooks.py    # inject a HookRegistry between steps
wendle nodes phone.json                         # list node ids (verified anchors marked)
wendle navigate phone.json --to <node-id>       # route to a node and verify arrival
wendle render phone.json --target dot           # offline, redaction-safe map (dot/flow/maestro/python)
```

Exit codes carry the result into the shell:

```
0   verified success        (replay completed / navigate arrived)
1   crash                   (an uncaught exception)
2   usage error             (bad flags, missing file, unknown node)
3   honest stop / refusal   (stopped / arrived_unverified / off_graph / ...)
```

`3` is separate from `1` so a script can tell a refusal to guess apart from a failure.

## Install

```bash
uv sync          # or: pip install -e .
```

You need a device reachable over `adb` (USB or wireless debugging). `uiautomator2` is
imported lazily, only when you construct a real driver, so tests and offline tooling need
no device.

## Quickstart (Python API)

```python
import wendle

# RECORD: walk the device by hand for 90s. Returns a navigable map and saves it.
# Connects to the device and calibrates touch input on its own.
graph = wendle.record(duration=90, out="phone.json")

# REPLAY: re-enact the recorded walk on the device.
result = wendle.replay("phone.json", wendle.U2Driver())
print(result.status)        # COMPLETED, or STOPPED (with a typed reason) if a step failed to verify

# NAVIGATE: route to any node in the map and confirm arrival.
outcome = wendle.navigate(graph, graph.anchors()[0], target_id, wendle.U2Driver())
print(outcome.status)       # ARRIVED, ARRIVED_UNVERIFIED, OFF_GRAPH, ...
```

Pass a serial with `wendle.U2Driver("RF8…")`, or set `ANDROID_SERIAL`. For tests without a
phone, use `wendle.FakeDriver`. Render the map with `wendle.render(graph, "map.dot")`.

## The whole phone is the map

Because the map models the device rather than one app, you address screens by node id
regardless of which app or surface they belong to. To reach a target, the navigator:

1. finds the nearest **anchor**, a node with a verified way to be forced into existence
   (an app's launcher entry, or a system keyevent such as HOME, back, or recents);
2. forces that anchor, then verifies its fingerprint before trusting it;
3. computes a weighted shortest path to the target over the recorded graph (so it prefers
   reliable routes over fewest hops) and walks it, re-observing and re-planning at each
   step.

Cross-app edges (a share sheet, an OAuth handoff, an OEM intent) are kept in the graph as
re-anchor checkpoints: when a route leaves one app for another, the navigator re-roots at
the destination app's anchor and continues. How to launch an app is decided by a ladder:
recorded component, then recorded launcher-icon tap, then package default, then monkey
launcher. The icon-tap rung is what reaches entries that share a process with another app
(for example an assistant inside the system Google app), where `am start` cannot.

```python
# Cross surfaces in one workflow: read a value in Settings, act on it in another app.
graph = wendle.Graph.from_json(open("phone.json").read())
driver = wendle.U2Driver()

# Each target is a node id; the navigator launches and routes to whichever app owns it.
wendle.navigate(graph, start, settings_node, driver)            # into a system surface
value = read_something(driver)                                  # your code, off the live screen
wendle.navigate(graph, settings_node, other_app_node, driver)   # across the app boundary
```

System surfaces (launcher, shade, quick settings) are reached by their keyevent anchors, so
a route can pass through them and not only through apps.

## Hooks

A hook runs between two replay steps, keyed by step index or by the screen wendle observes.
It reaches the device only through `ctx`, and returns a directive that decides what happens
next:

```python
from wendle.replay.hooks import HookRegistry, cont, stop, goto

hooks = HookRegistry()

@hooks.after(2)                          # fires after recorded step 2 has verified
def inspect(ctx):
    # ctx.driver is the device seam; ctx.node_id is the verified map node we are on
    # (None when the landing was ambiguous, so check it).
    reading = read_live_state(ctx)       # a Frida read, an LLM call, or plain Python
    ctx.emit("native_modules", reading.count)   # record a value-free fact on the result

    if reading.blocked:
        return stop("policy_blocked")    # halt instead of continuing
    if reading.needs_detour:
        return goto(other_node_id)       # reroute; the navigator pathfinds and verifies it
    return cont()                        # continue the recorded path (None also means cont)

wendle.replay("phone.json", wendle.U2Driver(), hooks=hooks)
```

- `@hooks.before(n)` and `@hooks.after(n)` fire immediately before and after a step;
  `@hooks.screen("pkg/.Activity")` fires once each time replay arrives at that screen.
- `cont()` continues the recorded path. `stop(reason)` halts with a value-free label; the
  framework will not continue past it. `goto(node)` hands control to the navigator, which
  pathfinds to the target and confirms arrival, returning a typed stop on an ambiguous
  landing rather than a claimed success.
- A hook reaches the device only through `ctx.driver` and steers only through its return
  value. Step indices always refer to the original recording, even after a `goto`, and a
  per-run budget bounds reroute loops.

`scripts/demo_hook_frida.py` is a working on-device Frida hook between two replay steps.
`examples/settings_assistant.py` routes a system map by node and steers a hooked replay
with `goto`, live reads, and a `stop`.

### Why not point an LLM agent at the app instead

An LLM agent re-decides every action on every run and can confidently tap the wrong thing.
With wendle the path is recorded and verified up front, and the agent is one hook at a known
point that reads state and reroutes, rather than the thing driving every tap.

## Outcomes

`replay` and `navigate` return typed results. Branch on the constants, not on strings.

- `ReplayStatus` is `COMPLETED` or `STOPPED`, with a typed `StopReason` on a stop
  (`ELEMENT_NOT_PRESENT`, `AMBIGUOUS_MATCH`, `CREDENTIAL_REQUIRED`, `HOOK_STOP`,
  `GOTO_FAILED`, and others).
- `NavStatus` is one of `ARRIVED` (confirmed), `ARRIVED_UNVERIFIED` (plausibly there but
  unconfirmable, so the caller decides), `OFF_GRAPH`, `CONTENT_DRIFT`, `CROSS_APP_BOUNDARY`,
  `FORCE_FAILED`, `NO_ROUTE`, `COORDINATE_ONLY_REFUSED`, or `CREDENTIAL_REQUIRED`.

A text-free structural match is reported as `ARRIVED` only when corroborated by a verified
interaction in the same call (a gated launch or a walked recorded edge). Without that
corroboration it could be an unrecorded look-alike screen (Inbox versus Archive), so it
degrades to `ARRIVED_UNVERIFIED`. A result's `repr` never contains a selector value or a
secret.

## How it works

- **Capture.** A manual recorder watches you drive the device. Scaled `getevent` is the
  primary signal: it reports that a physical tap happened and where, on every screen. Each
  tap binds to the settled hierarchy snapshot that was on screen at tap time via a
  timestamped ring buffer. Typed text becomes a `set_text` action, with password fields
  reduced to a `{param}` handle at capture, before the literal leaves the buffer.
- **Fingerprint.** Each screen gets a structural signature: a hashed tree of
  `(class, resource-id, clickable, content-desc shape)` with list children collapsed and
  volatile subtrees (clock, battery, badges) stripped, namespaced by foreground package and
  window. A separate text-free `structure_id` drives a graded match tier (EXACT, STRUCTURE,
  WEAK, UNVERIFIABLE) used to decide how confident an arrival is.
- **Graph.** A `networkx` `MultiDiGraph`, persisted to JSON as structure only: no
  callables, no secret literals. Routing is a weighted shortest path from the nearest
  forceable anchor.
- **Replay and verify.** A launch step plus a wait-then-verify loop with a tolerance band
  absorbs A/B and loading-screen variation without false aborts, and stops with a report
  when it lands off-graph.

## Prior art

wendle takes several pieces from existing tools:

- The launch and per-command wait-then-verify model is from Maestro's flow runner;
  `wendle render --target maestro` emits a Maestro flow.
- Re-observing and re-planning at each navigation step, and restarting at an anchor to
  recover, follow DroidBot's UI Transition Graph.
- The hashed structural-signature approach to fingerprinting is from APE and Fastbot.
- `set_checked` (read, modify, verify; flip only on a mismatch) follows Playwright's
  `setChecked` and the Appium check-then-click idiom.
- Attaching hooks to graph nodes by reference, rather than serializing them, mirrors
  LangGraph's `add_node(name, callable)`.

## Scope and limits

v1, in this repo: the manual recorder, deterministic replay, graph navigation, and the
inter-step hooks. Record, replay, and navigate are validated on a Galaxy S23, including
launching arbitrary apps (tested across 50), launching entries that share a process,
passing through system surfaces, and telling structural twins apart.

Limits:

- **Device-scoped, not fleet-portable.** A recording is bound to one device's calibration,
  resolution, and OS build. Semantic selector edges port across devices; structural
  fingerprints, coordinate-only edges, and force actions do not. One graph across a fleet
  is a v2 design (a logical graph plus a per-device overlay).
- **Multi-app handoff within a single recorded run** is not yet proven end-to-end on a
  device. The data model, routing, and per-app launch support it, but a live A→B→C handoff
  in one run has not been validated.
- **Compose, coordinate-only, and WebView.** testTag-less single-Activity Compose is the
  limit of structural fingerprinting; screens with no stable selector replay through flagged
  raw coordinates; WebView is low-confidence. Flutter and games are out of scope (no usable
  accessibility tree).
- **Maintenance is reduced, not removed.** An app UI change invalidates the affected
  screens' fingerprints, which then need re-recording. Re-recording a changed flow is
  usually cheaper than re-authoring the equivalent selector code.

Not built (planned for v2): crawling an app to build the map without a manual walk, and a
live observability dashboard.

## Development

```bash
uv run python -m pytest -q     # 844 tests against FakeDriver, no phone needed
```

The device sits behind a `DeviceDriver` port, so fingerprinting, gesture segmentation, and
the selector ladder are pure functions over driver output (XML in, hash out) and run in CI
against recorded-hierarchy fixtures, including adversarial ones (truncated trees, empty
dumps).
