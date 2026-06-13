# wendle

Drive any Android app from Python. You walk through the app once by hand;
wendle records that walkthrough, replays it on the device, and lets you
navigate back to any screen it has seen.

It works black-box over ADB and uiautomator2, so it needs no app source and
doesn't instrument the target. Elements are matched by content-desc, text, or
resource-id, and it only falls back to raw coordinates when nothing stable is
available.

The reason to record a walkthrough instead of writing a script is that you can
splice your own code into the gaps between steps. A hook can run a Frida read,
call an LLM, or run plain Python, look at the live state, and decide what
happens next: keep going, stop, or reroute to a different screen. The recorded
path stays fixed and deterministic; you only hand control to your own logic at
the points you choose.

When wendle can't confirm where it landed, it stops and tells you which step
and element it got stuck on, with a typed status. It won't report a success it
didn't verify.

### Why not just point an LLM agent at the app?

An LLM agent re-decides every action on every run and can confidently tap the
wrong thing. With wendle the path is recorded and checked up front, and the LLM
is one hook at a known point that reads state and reroutes, rather than the
thing driving every tap.

## Install

```bash
uv sync          # or: pip install -e .
```

You need a device reachable over `adb` (USB or wireless debugging) and
`uiautomator2`.

## Quickstart

```python
import wendle

# Record: walk the app by hand for 90 seconds. Returns a navigable map and
# saves it. Connects to the device and calibrates on its own.
graph = wendle.record(duration=90, out="myapp.json")

# Replay: re-run the recorded walk on the device.
result = wendle.replay("myapp.json", wendle.U2Driver())
print(result.status)          # COMPLETED, or a typed stop if a step couldn't be verified
print(result.stop_reason)     # the StopReason on a stop

# Navigate: route to any node in the map and confirm arrival.
outcome = wendle.navigate(graph, graph.anchors()[0], target_id, wendle.U2Driver())
print(outcome.status)         # ARRIVED, ARRIVED_UNVERIFIED, OFF_GRAPH, ...
```

Pass a serial with `wendle.U2Driver(serial)`, or set `ANDROID_SERIAL`. For
tests without a phone, use `wendle.FakeDriver`. Render the map with
`wendle.render(graph, "map.dot")`.

## CLI

The same verbs are available as a command (`uv run wendle ...`, or just
`wendle` once installed):

```bash
wendle record --out myapp.json --duration 90      # walk the app by hand; save the map
wendle replay myapp.json --param password=…       # re-enact it (credentials never logged)
wendle replay myapp.json --hooks my_hooks.py      # inject a HookRegistry between steps
wendle nodes myapp.json                           # list node ids (verified anchors marked)
wendle navigate myapp.json --to <node-id>         # route to a node and verify arrival
wendle render myapp.json -o myapp.dot             # offline, redaction-safe DOT map
```

Exit codes:

```
0   verified success
3   stopped or refused (stopped / arrived_unverified / off_graph / ...)
2   usage error
1   crash
```

A refusal (`3`) is kept separate from a crash (`1`) so a script can tell
"wendle wouldn't guess" apart from "wendle broke".

## Hooks

A hook runs between two replay steps, keyed by step index or by the screen
wendle observes. It reaches the device only through `ctx` and returns one of
`cont()`, `stop()`, or `goto(node)`:

```python
from wendle.replay import HookRegistry, cont, stop, goto

hooks = HookRegistry()

@hooks.after(2)                          # runs after recorded step 2
def decide(ctx):
    state = read_runtime_state(ctx)      # a Frida read, an AI-agent call, whatever you need
    if state.blocked:
        return stop("policy_blocked")    # halt instead of barrelling on
    if state.needs_detour:
        return goto(some_node_id)        # navigator pathfinds and verifies the reroute
    return cont()

wendle.replay("myapp.json", wendle.U2Driver(), hooks=hooks)
```

`goto()` goes through the navigator: it pathfinds to the target node and checks
it actually arrived, returning a typed stop on an ambiguous landing instead of
claiming success. `scripts/demo_hook_frida.py` is a working on-device Frida
hook between two recorded steps.

## Return values

`replay` and `navigate` return typed results. Branch on the constants, not on
strings:

- `ReplayStatus` (`COMPLETED` / `STOPPED`), with a typed `StopReason` on a stop.
- `NavStatus` (`ARRIVED` / `ARRIVED_UNVERIFIED` / `OFF_GRAPH` / `FORCE_FAILED` / ...).

The `repr` of a result never includes a selector value or a typed secret. A
password field shows up in the UI dump as plain text; the recorder replaces it
with a `{param}` handle and never stores the value.

## What's hard about this

Running code between steps is the easy part — LangGraph-style interrupt/goto
already does that in the abstract. The work in wendle is the Android side that
makes those steps land somewhere real on an app you don't control: replaying a
recording faithfully, recognising a screen you've seen before (including two
pages built from the same layout), confirming you actually arrived, launching
apps that share a process, and keeping secrets out of the recording.

## Scope

v1, this repo: record, replay, navigate, and the inter-step hooks. v2, not
built yet: crawling an app to build the map without a manual walk, and
generating a reusable navigation module from a recording. The design spec is in
[`docs/design/wendle-design-spec.md`](docs/design/wendle-design-spec.md).

## Development

```bash
uv run python -m pytest -q     # tests run against FakeDriver, no phone needed
```
