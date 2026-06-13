# Android Device-Navigation Mapper — Design Spec (v1)

**Status:** Settled design, adversarially validated and verified. Corrections folded in.
**Date:** 2026-05-31
**Platform:** Android only, via ADB + uiautomator2 (u2). Python.

> This document is the authoritative v1 spec. Where the brainstormed baseline survived
> verification it is stated as-is. Where verification graded a decision **risky**,
> **needs-revision**, **invalid**, or **broken**, the correction is **folded directly into the
> relevant section** (§3/§5/§6/§7/§9/§10/§11) — there is no separate risk register. Only the
> irreducible scope limitations and prior-art positioning are collected, in
> [§12 Known limitations & positioning](#12-known-limitations--positioning).

---

## 1. Overview & Intent

The Android Device-Navigation Mapper models the **entire phone as a state machine**, not a single
app. A **node** is any state the device can be in — lockscreen, home/launcher, system UI surfaces
(notification shade, quick settings, volume panel), Settings, an arbitrary app, a browser. An
**edge** is a recorded transition between two states. The v1 output is a persisted, replayable
**navigation multigraph** the library drives directly (a generated Python module is a deferred emitter
— §10).

It is a **building block**, consumed equally by:

- **QA automation** — deterministic replay of recorded device flows.
- **AI agents** — a stable navigation primitive an agent calls to reach a known screen, plus
  attach-point hooks for agent functions.
- **Security tooling** — Frida attach, HTTP/mitmproxy intercept, arbitrary `adb shell` injected at
  graph nodes/edges.

It replaces hand-written ADB/uiautomator scripts with a recorded, inspectable graph the library
replays.

### Who it's for (persona — gate-zero, satisfied)

**Developers who need Android automation that spans multiple apps and system surfaces on a device**,
but don't want to hand-build a bespoke automation every time with UIAutomator/Maestro, *and* don't
want to babysit an AI coding agent looping over screenshots to synthesize one. This is a **simple
Python library and framework** that reduces (does **not** eliminate — see below) that authoring hassle
while adding an **easy-to-implement custom layer** — so developers focus on their **top-layer needs
(Frida, AI agents, scraping, RPA)** instead of the underlying automation layer.

**Scope of "across … devices" (honest correction).** The differentiator is **cross-app / cross-surface
on a device** (whole-phone scope), NOT "one recorded graph that runs unchanged on a mismatched fleet."
A recorded graph is **device-scoped** (§12): semantic selector edges port across resolutions, but the
structural fingerprints, `coordinate_only` edges, and `force_actions` do not. Fleet use requires the
logical-graph / device-overlay split described in §12 — a **v2+ feature**, not v1.

**Why this over Maestro+MCP or an LLM agent:** Maestro/UIAutomator make you author each flow by hand,
per app, single-surface; an LLM-agent loop is nondeterministic and slow to develop against. This gives
a **recorded-once, deterministically-replayed, device-wide** navigation layer with **code-level hooks**.
**Honest caveats:** it does not eliminate maintenance (an app UI change invalidates affected
fingerprints → re-record those screens — but that re-record is typically cheaper than re-coding the
equivalent selectors, and Maestro flows break on the same change); and it is **not** a high-frequency
deep-random-access scraper (the Android anchor/walk constraint of §6 applies to every UI-driver,
Maestro and Appium included — use an API or a root bypass for that). Its value is real but **narrow**:
it pays off specifically when workflows are cross-surface AND reused AND want code hooks. (This
paragraph is the debate's gate-zero deliverable — see §14.)

**Terminology note (correction):** "navigation graph" / "device-wide navigation" collides with
Android's own developer-authored **Navigation Component**. Throughout this spec we use **observed
navigation graph** or **recorded device graph** to make clear this is an *observed/recorded* graph
of real device states, NOT a *declared* in-app navigation graph.

### Two modes

1. **CRAWL MODE = manually driven recorder.** The human navigates the device by hand; the program
   **observes**, fingerprints each screen, detects the action the user took, and records the
   transition. It is a **recorder, not an autonomous explorer.** (Autonomous "tap every button"
   exploration is explicitly **dropped** — see §2.)
2. **NAVIGATE MODE = autonomous replay.** Given a recorded graph, the program drives the device
   itself, replaying a known path from a `from` node to a `to` node, firing hooks at each step,
   verifying arrival by fingerprint, and stopping deterministically if it lands off-graph.

---

## 2. Goals & Non-Goals

### v1 Goals (scope)

- **Manual-record crawl** — human drives, tool observes and reconstructs the semantic action into a
  graph.
- **Autonomous replay** — deterministic `from → to` navigation over the recorded graph.
- **Python module output** — primary, library-first artifact (graph-shortest-path-derived
  functions).
- **Hierarchy-only sensing** — the uiautomator/accessibility tree is the only screen sensor in v1.
- **Credentials-as-input** — login walls handled by accepting credentials as parameters; typed
  values are captured as `set_text(selector, value)` actions (see §5), parameterizable not
  hardcoded, with **secret fields redacted at capture** (§4 — never stored as literals).
- **JSON persistence** — graph structure persisted to JSON (structure only; never callables, never
  secret literals).

### v1 Non-Goals (explicit)

- **Autonomous exploration** — no random/RL/LLM "tap everything" crawling. The manual recorder is
  the deliberate design choice and is also what makes the fingerprint problem tractable (§7).
- **Vision / OCR** — none in v1. Deferred to a later version for Flutter/games/opaque WebView.
- **Flutter & games** — out of scope (opaque single render surface; no usable accessibility tree).
- **CLI** — library-first, no CLI. A thin programmatic entrypoint suffices (§10).
- **Codegen** — **cut from v1** (debate consensus). The Navigator replays paths directly; the
  generated Python module is a strictly-dumber duplicate and the worst credential-leak sink. Deferred
  behind the graph→emitter seam (§10).
- **Live server + web dashboard** — **cut from v1** (debate consensus). v1 observability is an
  append-only NDJSON log (§3.1) + an offline static graph render. The live FastAPI feed + dashboard
  (observability-only, LangSmith-style) is **v2**.
- **Dashboard-driven control** — there is no dashboard in v1, and even in v2 the dashboard never
  mutates state: all control is code-side (§3.3 control-surface inventory).
- **Maestro output** — dropped from v1 (declarative YAML cannot host Python nav-time hooks). Kept
  as a *future pluggable emitter* behind the graph→artifact interface (§10).

---

## 3. Architecture

> **v1 is ONE layer + a log file.** The 5-reviewer design debate reached strong consensus to cut the
> live FastAPI server and the web dashboard from v1: nothing in the v1 acceptance criteria (§14) needs
> *live* streaming, and the live server/dashboard is the highest-cost, lowest-moat surface. v1 ships
> the **core library** plus an **append-only NDJSON event log**; a one-shot offline renderer turns the
> log/graph into a static graph image. The **live observability dashboard (LangGraph/LangSmith-style)
> is deferred to v2** — the NDJSON log is its on-disk precursor and keeps the event-envelope seam
> (§3.5) defined now.

```
v1 (ships):
┌──────────────────────────────────────────────────────────────┐
│ CORE LIBRARY (Python) — the ONLY authoring + control surface   │
│  graph build/load · crawl recorder · navigator · fingerprint   │
│  selector ladder · hook registry · device driver (u2)          │
│  control API (§3.3) · DeviceDriver port (testable, device-free)│
└───────────────┬──────────────────────────────────────────────┘
                │ append-only events (one-way, redacted §3.5)
                ▼
        ┌───────────────────────────────┐
        │ NDJSON EVENT LOG (file)        │  → offline one-shot
        │  versioned envelopes (§3.5)    │    graph render (static)
        │  doubles as the audit trail    │
        └───────────────────────────────┘

v2 (deferred): live FastAPI feed (SSE telemetry + WS screen-mirror) + web dashboard
               (observability-only, LangSmith-style) reading the same envelope.
```

- **The library is fully headless and standalone in v1.** Codegen is also cut from v1 (§10) — the
  Navigator already replays paths; the generated module is deferred behind the graph→emitter seam.
  Prove the headless core (record → fingerprint → navigate) end-to-end first (§14).

### 3.1 v1 event sink: append-only NDJSON log (live server deferred to v2)

v1 has **no network transport.** The recorder and navigator emit versioned event envelopes (§3.5)
**append-only to a local NDJSON file** (one JSON object per line). This is the entire v1 observability
surface, and it doubles as the security **audit trail** (§3.4). A separate **offline, one-shot**
renderer reads the finished log (or the persisted graph JSON) and produces a static graph image
(`networkx` → `graphviz`); no live streaming, no browser, no sockets.

**v2 (deferred) live feed**, when built, reads the *same* envelope and adds: **SSE** for read-only
telemetry (mandate HTTP/2; text-only), and a **WebSocket screen-mirror on its own independent
channel** (binary frames never multiplexed onto the telemetry stream). The v2 dashboard is
observability-only (§3.3). Specifying the envelope now keeps that seam stable without building the
server in v1.

### 3.2 Decoupled emission (the recorder must never block on the sink)

Event emission (crawl step 7, §5) is **fire-and-forget**, fully decoupled from device interaction.

- v1: append to the NDJSON log via a **bounded queue + background writer thread**; if the disk writer
  lags, drop-oldest rather than stall the recorder. Flush/fsync on session pause and close.
- v2 (when the live feed lands): the same bounded-queue discipline applies per subscriber
  (drop-oldest / coalesce, heartbeats, reconnection) so a lagging browser tab never stalls capture.

### 3.3 Control-surface inventory — ALL state mutation is code-side (blocker resolved)

The debate found the spec **self-contradicting** on where control lives (an "observability-only"
dashboard that §5.1 nonetheless routed edge accept/correct/drop through, plus a stray "server provides
control endpoints" line). Resolution: **every state-mutating operation is a synchronous library call
in code. There is no control surface anywhere else** — not in the v1 NDJSON log, not in the v2
dashboard. The table below is authoritative; if an operation is not listed as code-side, it does not
exist.

| State-mutating operation | Owner (v1) | API | NOT owned by |
|---|---|---|---|
| Start / pause / resume a crawl session | code | `session.start()` / `pause()` / `resume()` | log, dashboard |
| Re-sync the current node after manual move | code (inside `resume()`) | re-dump + re-fingerprint | — |
| Accept / correct / reject a `needs_confirmation` edge (§5.1) | code | `session.confirm_edge(id)` / `correct_edge(id, selector)` / `reject_edge(id)` | dashboard |
| Attach / detach a hook (§9) | code | `graph.on_node(...)` / `on_edge(...)` | log, dashboard |
| Re-tune `fingerprint_config` (forces re-hash) | code | re-record / re-key | — |
| Trigger codegen / offline graph render | code | `mapper.render(...)` (codegen deferred, §10) | — |

- `pause()` halts the observe loop (stops consuming input events / suspends steps 1–8 of §5).
  `resume()` re-dumps and re-establishes the current node before resuming capture. Because these are
  function calls, ordering/acknowledgement is trivial (just a return) — none of the
  connection-aware-control-path complexity the verification flagged.
- **`needs_confirmation` edges (§5.1) are resolved by the `confirm_edge` / `correct_edge` /
  `reject_edge` API, not by a UI.** In v1 the human reviews them by reading the NDJSON log (or the
  static render) and calling the API; provisional edges are never auto-committed. A v2 dashboard would
  be a *thin caller* of this same code API, never a parallel control path.
- The v2 dashboard, when built, is **pure observability (LangSmith-style)** — it reads the envelope
  and renders; it issues no mutations. **Author + control in code, observe in dashboard.**

### 3.4 Security (v1 = on-disk; networked surface deferred with v2)

v1 has no network surface (§3.1), so v1 security is **at-rest**, and it matters: the NDJSON log and
the graph JSON together are a **behavioral map + raw UI-hierarchy snapshots** of a real device/account.

- **Redaction-by-default (§4)** keeps secret literals out of the log, graph, and codegen entirely.
- **At-rest:** write graph/log with `0600` perms; emit a `do-not-commit — contains a behavioral map
  and raw hierarchy PII` header; run a PII-minimization pass over stored hierarchy XML; document a
  retention policy. Stop describing the JSON as casually "diffable/reviewable" without this caveat.
- **Privileged hooks (§9)** — Frida / mitmproxy / arbitrary `adb shell` / root `am instrument` bypass
  pass through a **capability gate + audit-log entry** (in the same NDJSON log), distinct from ordinary
  navigation, so the security-tooling framing cannot silently become a surveillance tool.

**v2 (deferred) networked surface** — when the live server + screen-mirror land, they become a
full-device-control-adjacent surface and inherit non-negotiable rules: default bind `127.0.0.1`;
any non-loopback bind requires an auth token + TLS; the screen-mirror channel is auth-gated; and the
v1.1 AccessibilityService socket (§5.x) authenticates its `adb forward` handshake and is session-scoped
(it is a documented keylogging surface). These are specified now so v2 cannot ship them unreviewed.

### 3.5 Event envelope (the NDJSON log line schema)

Define a stable, **versioned event envelope** now — it is the v1 NDJSON log line, the v2 live-feed
payload, and the integration seam for all consumer types:

```
{ "v": 1, "event_type": "...", "timestamp": "...", "session_id": "...",
  "screen_id": "...", "transition": {...}, "hook": {...},
  "alert": { "kind": "fingerprint_mismatch" | "off_graph" | ... } }
```

The envelope carries `transition`/`action` payloads, so the **§4 redaction invariant applies here
too**: a `sensitive` action's literal is never serialized into an event — only its `{param: …}`
handle. This holds whether the envelope is streamed live or written to the append-only NDJSON log
(§3.1).

---

## 4. Data Model

The graph is a **directed multigraph** (two screens may have multiple distinct edges). **Hooks are
stored separately**, keyed by screen id or edge id, so graph structure stays clean. Persisted to
**JSON — structure only**.

### Screen (node)

| field | meaning |
|---|---|
| `id` | structural fingerprint hash (see §7); the node key |
| `screen_type` | auto-detected: `homescreen` / `app` / `lockscreen` / `settings` / `systemui` / … |
| `package` | foreground package |
| `activity` | foreground activity (coarse signal; see §7 for where it breaks) |
| `hierarchy` | raw XML snapshot at record time |
| `actions` | list of Level-1 UI-element actions found at record time (§8) |
| `force_action` | **optional**, **verified-at-record-time** direct way to reach this node (§6) |
| `fingerprint_confidence` | `high` / `medium` / `low` / `out_of_scope` (§7, §13) |
| `volatile` | bool — screen never settled to a stable hash (animation); reduced-subtree hash used |

### Action

| field | meaning |
|---|---|
| `selector` | best stable handle (structured: `{text:…}` / `{content_desc:…}` / `{resource_id:…}` / `{xpath:…}` / `{coords:…}`) |
| `action_type` | `click` / `long_click` / `swipe` / `scroll` / **`set_text`** (text entry, §5) / `keyevent` |
| `value` | for `set_text`: a **parameter handle** (e.g. `{param: "username"}`), **not a raw literal** — see Redaction below. For non-sensitive fields the captured literal may be stored; for sensitive fields it is **never** stored |
| `sensitive` | bool — set at capture when the target field is a secret (password/OTP); forces `value` to a parameter handle and bars the literal from JSON/log/codegen |
| `replayability` | **`high` / `medium` / `coordinate_only`** — capture-time confidence (see below) |

**The Action carries its own replay method via its selector.** There is **no separate `method`
field on Transition** (removed as redundant). The selector-ladder resolution lives in the library
shim, not frozen into the Action.

**Redaction-by-default (BLOCKER fix — secrets never get recorded).** Text entry (§5 step 6) is
captured by diffing `EditText` text, which would otherwise write passwords, OTPs, and PINs straight
into `Action.value`, the JSON, the event log, and generated code. This is barred at capture: a field
is flagged `sensitive` when **any** of these Android signals is present on the focused node/window —
`AccessibilityNodeInfo.isPassword()` is true, the `EditText` `inputType` carries a password variation
(`textPassword` / `textVisiblePassword` / `numberPassword`), or the window has `FLAG_SECURE`. For a
sensitive field the recorder stores **only a parameter handle** (`value = {param: "<field_name>"}`)
and the field's structural selector — **the literal is discarded before it ever leaves the capture
buffer** and never reaches `Action.value`, the JSON graph, the §3.5 event envelope/NDJSON log, or
codegen. At replay/navigate time the consumer supplies the secret out-of-band (ties to the v1
"credentials as input" decision). Note this is also a **correctness** fix: a soft-keyboard pre/post
text-diff over a masked password field captures a masked/garbled string anyway, so storing it would
be both unsafe and wrong.

**`replayability` confidence (correction, from driver-capture verification):** if the only handle
recovered at capture time is **raw coordinates** (no text / content-desc / resource-id and no
accessibility source), the action is tagged `coordinate_only` / low-confidence. The Navigator
**warns** (and the NDJSON log records it) instead of attempting a brittle coordinate replay. This encodes the
"coordinates last, never default" rule directly into the data model — some screens are
**recordable-as-transition but NOT reliably replayable in v1**, and that is surfaced, not silently
recorded.

### Transition (edge)

| field | meaning |
|---|---|
| `source` | source screen id |
| `target` | target screen id |
| `action` | the `Action` that caused the transition |
| `weight` | replay cost/reliability (see §6 — drives shortest-path) |
| `needs_confirmation` | bool — set when the tap-to-hierarchy binding was LOW confidence (§5.1); a provisional edge resolved via the code-side `confirm_edge`/`correct_edge`/`reject_edge` API (§3.3), surfaced in the NDJSON log, never auto-committed as stable |

### Graph

- `MultiDiGraph` (NetworkX). Two nodes may have parallel edges.
- `hooks` — separate registry keyed by screen id / edge id (callables, **never serialized** — §9).
- `fingerprint_config` — the per-package / per-screen_type knob set used (§7), **persisted** so
  replays are reproducible.
- `device_profile` — per-device calibration (input node, coordinate scaling — §5), persisted.

### JSON shape (structure only)

```json
{
  "v": 1,
  "device_profile": { "touchscreen_node": "/dev/input/eventX",
                      "abs_x": [0, 4095], "abs_y": [0, 4095],
                      "display": [1080, 2400] },
  "fingerprint_config": { "<package_or_screen_type>": { "include_text": false,
                          "include_content_desc_values": false, "list_collapse": true,
                          "compressed": false, "max_depth": 50,
                          "resource_id_denylist": [ "...:id/clock", "...:id/battery" ] } },
  "screens": [ { "id": "...", "screen_type": "app", "package": "...", "activity": "...",
                 "fingerprint_confidence": "high", "volatile": false,
                 "force_action": { "kind": "am_start", "value": "pkg/.MainActivity",
                                   "verified": true },
                 "actions": [ { "selector": {"text": "BanCoppel"}, "action_type": "click",
                                "replayability": "high" },
                              { "selector": {"resource_id": "...:id/password"},
                                "action_type": "set_text", "sensitive": true,
                                "value": {"param": "password"} } ] } ],
  "transitions": [ { "source": "...", "target": "...",
                     "action": { "selector": {"text": "BanCoppel"},
                                 "action_type": "click" }, "weight": 1.0 } ]
}
```

**Invariant:** JSON contains ONLY structure (ids/fingerprints, transitions, `force_action` strings,
screen_type/package/activity, recorded actions + selectors, config, device profile). **Callables
are never serialized** (§9), and **secret literals are never serialized** — a `sensitive` action
stores only a `{param: …}` handle (§4 Redaction), as shown by the `password` action above.

---

## 5. Crawl Mode — Manual Recorder

> **This is the riskiest dimension.** The verification verdict **holds**, but inverts two framings
> from the baseline. Read this section and §12 carefully.

### The 8-step loop

1. `dump_hierarchy()` → raw XML (pinned config — see §7: `compressed=False` default, fixed
   `max_depth`).
2. **Fingerprint** the screen → screen id (§7).
3. **If new screen:** add node to graph; run "first-seen" crawl hooks (§9, e.g. androguard
   manifest parse on first `app` node).
4. **Detect what action the user just took** — the hard part (see below).
5. **Record the transition** (`source → action → target`).
6. Run any crawl hooks attached to this node/edge (off the loop via `asyncio.to_thread`/worker so a
   slow hook never stalls capture — §9).
7. **Emit event** (fire-and-forget into the bounded queue — §3.2; written to the NDJSON log §3.1).
8. **Wait for next user action** → back to 1.

### Action detection (step 4): layered capture — **getevent is PRIMARY, a11y is OPTIONAL enrichment**

> **Inversion correction (load-bearing).** The validation proposed an AccessibilityService as the
> *primary* semantic source with getevent demoted to fallback. Verification **refuted** that
> ordering. `TYPE_VIEW_CLICKED` is itself unreliable — a parent view can decline to dispatch it; it
> fires on a *logical* accessibility click, not on every *physical* tap; and it is silent on
> custom-drawn Compose, WebView internals, games, scrims, drag handles, and map canvases — the SAME
> blind spots the hierarchy has, PLUS it misses the human's raw tap entirely on those screens.
> Therefore:
>
> - **PRIMARY / floor signal = scaled `getevent`** — the only source that reliably knows a physical
>   interaction happened and where, on EVERY screen.
> - **OPTIONAL enrichment = a bundled AccessibilityService** — when it *does* fire, it hands you the
>   semantic node (text, content-desc, viewIdResourceName, bounds) directly, skipping
>   coordinate→bounds matching. It is opt-in (see friction below), never the trunk of the
>   architecture.

**Mechanism (Hybrid "C", corrected):**

1. **Calibrate the device first (mandatory onboarding, not optional).** Run `getevent -lp` (or
   `-pl`) once per connected device to:
   - **Discover the touchscreen input node** — a phone exposes several `/dev/input/eventN` nodes;
     probe for the one carrying `ABS_MT_TOUCH_MAJOR` / `ABS_MT_POSITION_X`. **Never hardcode
     `eventN`.**
   - **Read `ABS_MT_POSITION_X/Y` min/max.** getevent X/Y are **touch-panel virtual coordinates,
     NOT screen pixels.** You **must** scale raw → pixel before any bounds correlation. The panel
     max does **not** always equal display resolution and differs per device. Cross-check the panel
     max against the u2 window/display size and **warn** on mismatch.
   - Persist this as the per-device `device_profile` (§4).
2. **Capture the raw primitive from getevent.** It emits Linux multi-touch **type-B** protocol
   (`ABS_MT_SLOT`, `ABS_MT_TRACKING_ID`, `ABS_MT_POSITION_X/Y`, `BTN_TOUCH`, terminated by
   `SYN_REPORT`). Reconstruct gestures from the raw stream, not discrete taps.
3. **Segment & classify gestures** using `BTN_TOUCH` down/up + `SYN_REPORT` boundaries and
   `ABS_MT_SLOT`:
   - **tap** — short dwell, small displacement.
   - **long-press** — dwell > threshold.
   - **swipe/scroll** — displacement.
   - **multi-finger** — explicitly **flag/skip in v1** (do not mis-record).
   - **system keys** — home/back/recents = keyevents **3 / 4 / 187** are distinct keycodes; no
     hierarchy matching needed.
4. **Correlate against the PRE-ACTION hierarchy, never the post-transition dump.** `dump_hierarchy`
   is async to the human tap and lags badly on animating UIs. Freeze the last **settled** hierarchy
   as the "pre-action state" and correlate the (scaled) tap point against THAT. Detect "settled" via
   the a11y service's `TYPE_WINDOW_STATE_CHANGED` (if enabled) or a short hierarchy-stability poll.
   See the timing reconciliation in §5.1 — this conflicts with fingerprint double-dump and must be
   resolved.
5. **Enrich with the AccessibilityService when available.** If the bundled service is enabled, it
   streams `TYPE_VIEW_CLICKED` / `TYPE_VIEW_LONG_CLICKED` / `TYPE_VIEW_SCROLLED` + `getSource()` node
   info and `TYPE_WINDOW_STATE_CHANGED` over a local socket. When a click event fires, take its
   semantic source as the selector (cheaper and more reliable than bounds matching). When it does
   **not** fire (Compose/WebView/game/custom), fall back to getevent→bounds correlation. The service
   declares `canRetrieveWindowContent` and **must NOT enable touch-exploration** (that changes how
   the human interacts and breaks passive recording).
6. **Text / IME entry is a dedicated action type (blocker mitigation).** getevent + hierarchy
   **cannot** recover typed strings: soft keyboards "will never send any key event" on Jelly Bean+,
   and per-key IME nodes carry no stable selector. Instead: detect that focus is in an `EditText`
   (or the IME window is up) in the pre-action state, and capture the field **value by diffing the
   `EditText` text** pre/post → record `set_text(selector, value)`. Tie this to the v1
   "login walls accept credentials as input" decision so values are **parameterizable**, not
   hardcoded. Replay via u2 `send_keys` / `set_text`.
   - **Redaction-by-default (mandatory, §4).** Before any captured text is written anywhere, check
     `isPassword()` / password `inputType` variations / `FLAG_SECURE`. If sensitive, set
     `Action.sensitive=true` and store **only** `value = {param: "<field_name>"}` — the literal is
     discarded in the capture buffer and must never reach `Action.value`, the JSON graph, the §3.5
     event log, or codegen. (Also a correctness win: a masked-field text-diff yields a garbled string
     anyway.)

### 5.1 Timing reconciliation: action-detection vs fingerprint stabilization (correction)

Verification surfaced a real conflict the validation treated as independent. The fingerprint
double-dump stabilization (§7) requires two sequential dumps + a settle delay before recording a
node (`dump_hierarchy` can take ~3s on complex/animated UIs). Step-4 action detection requires the
hierarchy to be **current at tap time**. Naively chaining them (~6s+) lets a human tapping at human
speed outrun the recorder, binding a tap to a **stale pre-settle** hierarchy → wrong selector.

**Resolution (the spec MUST state which snapshot step-4 uses):**

- Maintain a **rolling buffer of recent settled hierarchies** (realized as the ring buffer
  specified below) updated by the stabilization step. Step-4 correlation always uses the snapshot
  whose validity window contains the tap — it does **not** trigger a fresh double-dump per tap.
- The double-dump stabilization runs on **screen entry** (after a transition settles), producing
  both the node's fingerprint *and* the next pre-action snapshot in one pass.
- On detected animation (never-idle), skip correlation for that frame and mark the screen
  `volatile`; fall back to activity/window namespace + reduced-subtree hash.
- The a11y `TYPE_WINDOW_STATE_CHANGED` signal (when available) is the cheap "screen changed,
  re-stabilize now" trigger that keeps the rolling snapshot fresh without per-tap polling.

**Tap-to-hierarchy binding (timestamp-window correlation).** A tap is never correlated against "the latest" hierarchy, because `dump_hierarchy` lags (hundreds of ms, worse during animation) and the latest snapshot may belong to the wrong screen. Instead the recorder maintains a fixed-size **ring buffer** (default 8) of recent snapshots, each stored as `(t_dump_start, t_dump_end, hierarchy_hash, hierarchy_xml)`.

All timestamps are normalized to a single **device `CLOCK_MONOTONIC` timebase** (the boot-relative clock that excludes deep-sleep; the same clock that backs `SystemClock.uptimeMillis()`). Three sources feed this timebase: (1) `getevent -lt` emits each input event's time as `sec.usec` in `CLOCK_MONOTONIC` (microsecond precision, not wall-clock), so the `SYN_REPORT` terminating a tap gesture yields an on-device monotonic `t_tap`. This holds only because the input fd's clock is set to `CLOCK_MONOTONIC` via `EVIOCSCLOCKID` — the kernel evdev default is `CLOCK_REALTIME`; AOSP `getevent` sets the monotonic clock, and the recorder must use such a build (or set the ioctl itself if it reads `/dev/input/eventX` directly), otherwise `t_tap` arrives in the wrong timebase and binding silently breaks. (2) The dump boundaries `t_dump_start`/`t_dump_end` are **stamped on-device in `CLOCK_MONOTONIC` by the uiautomator2 agent that performs the dump**, so they live natively in the same timebase as `t_tap` with no host↔device conversion. (Fallback if on-device stamping is unavailable: bracket the dump with host-side reads of the device monotonic clock and treat the adb round-trip latency — tens of ms and jittery, not microseconds — as the dominant uncertainty on the dump boundary; do not assume microsecond alignment. `/proc/uptime` is **not** used as the monotonic source, because its first field includes suspend time and is therefore `CLOCK_BOOTTIME`/`elapsedRealtime`, not `CLOCK_MONOTONIC`.)

Each tap binds to the **newest ring entry whose validity window `[t_dump_end, next_transition_or_now)` contains `t_tap`**, and the semantic selector is recovered by correlating the scaled tap point against *that* snapshot's hierarchy (per the §5 calibration) rather than the live one. When the opt-in AccessibilityService is enabled (§5.x), its `TYPE_WINDOW_STATE_CHANGED` / `TYPE_WINDOW_CONTENT_CHANGED` events provide authoritative epoch boundaries: `getEventTime()` is backed by `SystemClock.uptimeMillis()` — the same `CLOCK_MONOTONIC` timebase — so they segment the tap stream into per-screen epochs with zero extra reconciliation. Without it, epoch boundaries are inferred from `hierarchy_hash` changes between consecutive ring snapshots — coarser, bounded by dump cadence, which widens the guard band rather than failing.

A binding is **HIGH confidence** when `t_tap` falls cleanly inside exactly one snapshot's validity window with no screen-change boundary within a configurable guard interval (default 250 ms, sized to exceed the dump-boundary timing uncertainty) on either side. It is **LOW confidence** when a transition boundary lies within the guard interval, when two candidate windows straddle the tap, or when no settled snapshot's window contains `t_tap` (a transition was in flight). LOW-confidence transitions are **never silently committed as a stable selector**: they are written to the graph as provisional edges carrying the candidate selector(s) with `needs_confirmation=true`, emitted to the NDJSON log, and resolved by the human via the code-side `confirm_edge` / `correct_edge` / `reject_edge` API (§3.3) — not a UI. Fast multi-step tapping, and any clock-alignment slop, degrade into flagged-for-review edges, not wrong selectors.

### AccessibilityService onboarding friction (correction — NOT near-free)

Verification refuted the validation's rosy "bundle an a11y service" framing. A third-party
AccessibilityService on modern Android:

- **Cannot be enabled programmatically / via ADB** in the general case — the user must manually
  toggle it in **Settings → Accessibility**.
- On **Android 13+** hits the **"Restricted setting"** block for sideloaded APKs (extra manual
  steps).
- Is actively flagged / auto-disabled by **Play Protect** and OEM skins.

Therefore the a11y service is **opt-in with real onboarding friction**, never a default dependency.
The **scaled-getevent + pre-action-hierarchy hybrid is the dependable v1 floor and ships as the
default.**

### 5.x AccessibilityService enrichment (OPTIONAL; protocol specified, path gated to v1.1)

The getevent + last-settled-hierarchy floor above is the sole required capture path and is fully
functional on its own. An opt-in, device-side `AccessibilityService` may additionally stream semantic
UI events to raise selector confidence; it is **enrichment-only** and **entirely optional**. If the
service is absent, disabled, or unreachable, capture degrades to the floor with no loss of core
function. The enrichment **path** (the device APK + correlation logic) ships in **v1.1**; its wire
protocol is specified here so it is no longer an open design item and so the v1 capture pipeline can
be built with the integration seam already defined.

- **Transport.** The service hosts a loopback TCP `ServerSocket` on `127.0.0.1:<deviceport>` inside
  its own app process (needs only the auto-granted `INTERNET` permission). The Python host attaches
  with `adb forward tcp:<freeport> tcp:<deviceport>` and connects as the TCP client — the **same**
  mechanism u2 v3/atx-agent (7912) and appium-uiautomator2-server (6790) use. Deliberately **not** an
  abstract-namespace `localabstract:` socket (SELinux-sensitive for `untrusted_app` on non-rooted
  builds). `<deviceport>` default 8645; `<freeport>` must be freshly allocated so it never collides
  with u2's own forward. One client at a time; listener binds `SO_REUSEADDR`.
- **Message format.** Newline-delimited JSON (NDJSON), UTF-8, one event per line:
  `{"type":"VIEW_CLICKED","ts":...,"pkg":...,"class":...,"text":...,"contentDesc":null,"viewIdResourceName":...,"bounds":[l,t,r,b]}`.
  `type` ∈ {`VIEW_CLICKED`,`VIEW_LONG_CLICKED`,`VIEW_SCROLLED`,`WINDOW_STATE_CHANGED`,`WINDOW_CONTENT_CHANGED`};
  `bounds` from `getBoundsInScreen()` in screen pixels; `text`/`contentDesc`/`viewIdResourceName` are
  `null` when absent. Fields are serialized **synchronously inside `onAccessibilityEvent`** from
  `event.getSource()` (the `AccessibilityNodeInfo` is ephemeral — never retained or read off-thread).
  Service config: `android:canRetrieveWindowContent="true"` (required, else `getSource()` is null) and
  `accessibilityFlags` including `flagReportViewIds` (required to populate `getViewIdResourceName()`).
- **Lifecycle.** Binds the listener at service-connect; while no client is connected, events go to a
  bounded newest-wins ring buffer and are **dropped** on overflow (never block the UI, never persist).
  On disconnect it returns to `accept()`; the host reconnects by re-establishing the forward.
- **Use in correlation.** Each NDJSON record is matched to the already-captured scaled-getevent tap by
  timestamp proximity + `bounds` containment, and used only to confirm/recover the selector (notably
  `viewIdResourceName`). getevent remains the primary geometric source; the a11y record never
  overrides it.

---

## 6. Navigate Mode — Autonomous Replay

> Verification verdict **holds**; the baseline rule "force, don't verify" is **refined** to "force,
> THEN verify the anchor."

`navigate(graph, from, to)` — the Navigator never **assumes** current device state.

### Reaching the `from` node

- If `from` has a **verified** `force_action`, **force the device there directly** (don't pre-check
  where the device is) — **but then verify the anchor fingerprint before starting the walk** (see
  below).
- If `from` has no `force_action`, compute the shortest path from the **nearest anchor** (a node
  that *does* have a verified force_action), force to that anchor, verify it, then walk to `from`.

### force_action reliability (corrected — was over-optimistic)

**Blocker confirmed:** `adb shell am start` runs as shell uid 2000 and **cannot launch
non-exported activities** — `SecurityException: Permission Denial`, even for explicit `-n` component
intents. Since **Android 12 (API 31)** every component with an intent-filter must declare
`android:exported`, and the recommended default is `exported=false` for everything except the
LAUNCHER activity. **Net: for an arbitrary third-party app, the only reliably force-able activity is
its MAIN/LAUNCHER activity.** Deep internal nodes are overwhelmingly non-exported.

Additional confirmed failure modes:

- **Exit-code unreliability:** `am start` frequently returns exit code 0 even when it printed a
  Permission Denial or "Activity not started" to stderr, and even on success an extra-dependent
  activity (`getIntent().getExtras()` read in `onCreate`) renders blank/half-initialized. **"Force
  without verify" cannot detect this.**
- **Deep links (`am start -a android.intent.action.VIEW -d <uri>`):** work ONLY for activities whose
  intent-filter has `ACTION_VIEW` + `BROWSABLE` + `DEFAULT` + a `<data>` scheme **and**
  `exported=true`. A deep-link landing screen can also have a different back stack / synthetic parent
  → different fingerprint.

**Corrected `force_action` rules:**

1. `force_action` is a **capability that exists for SOME nodes, not most.** Never synthesize an
   unverified `am start -n` for an internal activity and assume it lands.
2. **Verified-at-record-time only.** On first seeing an `app` node, a crawl hook (a) launches
   MAIN/LAUNCHER, (b) parses the manifest via androguard to enumerate exported + BROWSABLE deep-link
   filters, (c) for each candidate, **fires it and stores it as `force_action` ONLY if the resulting
   fingerprint matches** the node it claims to reach. Flag deep-link-reached nodes as possibly having
   a different back stack.
3. **System keyevents are the reliable forces:** `keyevent 3` (HOME), `4` (back), `187` (recents)
   are SystemUI-level, not gated by app export. **Home is just a node with `force_action: keyevent 3`
   — not special.**
4. **`keyevent 26` (POWER) is NOT idempotent** — it *toggles*. Do not use a bare 26 as a set-state
   force. Read power/lock state first (`d.info['screenOn']` / `dumpsys`) and drive deterministically;
   model `screen-off` as its own node reached only after confirming current state.
5. **Refined rule: "don't assume current state; force the anchor; THEN verify the anchor
   fingerprint before walking."** Forcing is still unconditional (no pre-check of where the device
   is), but the post-force landing **must** be fingerprint-verified — the only way to catch silent
   `am start` failures.
6. **Optional root / `am instrument` bypass** for launching non-exported activities (root shell
   uid 0, or instrumentation in the app's own process) — **off by default**, explicit opt-in for the
   security-tooling consumer.

### Shortest-path-from-nearest-anchor (verdict: valid)

This is exactly `networkx.multi_source_dijkstra(MultiDiGraph, sources=<force-able anchors>,
target=from_node)` (NetworkX 3.6.x — pin it). Returns `(distance, path)` in one pass; `MultiDiGraph`
supported.

- **Edge weights encode replay cost/reliability** (verified force/deep-link edge cheapest →
  multi-tap chains more expensive → `coordinate_only` edges most expensive) so Dijkstra prefers
  **robust** routes, not fewest hops. Default weight 1 is pure BFS-by-hops and ignores reliability —
  don't.
- **MultiDiGraph parallel-edge resolution (permanent API constraint, not a pending fix).** A custom
  weight function on a `MultiDiGraph` receives the **aggregated** edge dict and **cannot** tell which
  parallel edge was chosen. (networkx#7582 is **CLOSED as intended behavior**, not a tracked bug.)
  After pathfinding, **re-resolve each hop's concrete edge/Action**:
  `min(G[u][v], key=lambda k: G[u][v][k].get('weight', 1))`. Codegen depends on this to emit the
  correct Action per hop — document it as fixed.

### Per-step arrival verification: wait-then-verify with tolerance (corrected — was naive equality)

A naive "dump, hash, compare" is flaky. Spec the verification as a **wait-then-verify loop**:

1. **Wait** for the activity to match (`d.wait_activity(activity, timeout)`) and/or known
   loading-spinner selectors to go gone (`.wait_gone(timeout)`). `UIAutomator.waitForStable` only
   watches the tree and does NOT guarantee true idle — background work continues.
2. **Debounce:** poll-dump until the structural fingerprint is **stable across two consecutive
   dumps** or the per-edge wait budget elapses.
3. **Verify with a TOLERANCE band, not strict equality.** A/B / server-driven screens legitimately
   produce **different** fingerprints for the "same" node; strict equality false-negatives and
   aborts valid navigations. Allow a fuzzy match (activity + dominant-subtree / top-level-structure
   match) to absorb A/B variants. Low-`fingerprint_confidence` nodes (§7) trigger a **wider match
   tolerance**.
4. **One retry:** re-resolve the selector via the ladder and re-tap once before declaring off-graph
   (the element may simply need to finish laying out).
5. **Off-graph** is declared only when neither the target fingerprint nor any expected neighbor
   matches after the full wait/retry budget. Then **stop and report** with rich context (expected
   fingerprint, observed fingerprint, current activity, screenshot). **No autonomous recovery in
   v1** (consistent with the dropped autonomous-exploration decision).

Make wait/retry budgets **per-edge configurable** (`d.settings['wait_timeout']`, default 20s; a
payments-confirm screen needs a longer network wait than a settings toggle).

### Non-deterministic dialogs + the dump-contention race (correction)

Register a persistent **u2 watcher set BEFORE navigation begins** to auto-dismiss incidental dialogs
(runtime permission prompts → Allow / While using; "App keeps stopping" / ANR; system-update nags;
rate-app popups), with a back-press fallback. Distinguish **expected dialog nodes** (modeled in the
graph) from **incidental dialogs** (watcher-dismissed).

> **Dump-contention race (load-bearing risk the validation understated).** A u2 watcher runs a
> background daemon thread that calls a **full `get_page_source()` every polling interval (~2s)** —
> it is NOT merely "fires on selector match," and its own code does not serialize against other
> dumps. The Navigator's verify loop **also** polls `dump_hierarchy`. UIAutomator's dump is a
> **single serialized channel** to the on-device accessibility service; concurrent dumps from the
> watcher thread and the verify loop contend, return stale/partial trees, slow both, or throw —
> manufacturing the very fingerprint flakiness the verify loop exists to prevent.
>
> **Mandatory mitigation:** serialize all dumps under a **single shared lock**, AND/OR **pause the
> watcher during the post-action settle/verify window**, AND/OR use a deliberately **slow watcher
> interval**. This applies to crawl mode too (the recorder's stabilization dumps must not race any
> background watcher).

---

## 7. Screen Fingerprint

> **The core hard problem**, now **device-wide** not app-scoped. Verification verdict **holds**, with
> two corrections (compressed-flag default; timing — see §5.1).

### The structural signature

A structural signature = a tree of `(class, resource-id, clickable, content-desc-shape)` tuples,
**dropping all values/text/bounds** and **collapsing repeated list children to one**, hashed.
Combine with a **coarse foreground signal** as a namespace.

This is the right family (matches Fastbot's hashed GUI-state and APE's attribute-path equivalence).
But the **canonical lesson of APE is that no single fixed attribute set works across all screens** —
the same fixed reduction is simultaneously too coarse for some screens (over-merge) and too fine for
others (under-merge), which is exactly why APE replaced fixed abstraction with a runtime-tuned
decision tree.

### Ship a configurable PIPELINE, not one fixed tuple (correction)

- **Default structural hash:** `(class, resource-id, clickable, content-desc presence/shape)`, with
  list-child collapsing and a resource-id volatility denylist.
- **Per-namespace knobs** (keyed by `package` or `screen_type`, **persisted in
  `fingerprint_config`**): include text? include content-desc *values* (not just shape)? include
  toolbar/title text? `max_depth`? list-collapse on/off? Keep text **OUT** of the default (avoids
  list-item under-merge) but allow **promoting specific nodes** (toolbar/title) back IN as a knob
  when structure alone over-merges. (Fastbot deliberately keeps text + resource-id; that is a valid
  point on the same knob.)

### Foreground signal: coarse PREFIX/namespace, NOT an authoritative key (correction)

Source the namespace from **BOTH** `dumpsys activity` (`mResumedActivity`) **AND** `dumpsys window`
(`mCurrentFocus` window name) so non-activity SystemUI surfaces (NotificationShade, volume dialog,
quick settings, keyguard/lockscreen) get **distinct namespaces**.

Where the activity signal **breaks** (do not rely on it):

- **Single-Activity + Jetpack Compose Navigation** (now the *recommended* Android architecture) —
  the resumed activity stays **constant** across every in-app destination; `dumpsys activity` cannot
  disambiguate any of them, and the Compose route is not exposed. **The entire burden falls on the
  structural hash** exactly where Compose is weakest (generic container classes, often no
  resource-id). This is the genuine **correctness ceiling** for Compose-heavy apps (see §12, §13).
- **SystemUI windows** (shade, quick settings, volume, keyguard) are **not** resumed activities;
  `mResumedActivity` shows the activity *underneath*. `RecentsActivity` is a known case where the
  resumed-activity signal does not update correctly → use the window-focus namespace.
- **`dumpsys` parsing fragility:** `mFocusedApp`/`mCurrentFocus` can return two entries on
  foldables/multi-window; trailing commas break naive parsing.
- **`u2.app_current()` RAISES `DeviceError`** (not a sentinel) when focus can't be determined. Wrap
  in try/except, fall back to the window-focus parse, and **never** let a namespace-lookup failure
  crash the recorder mid-crawl.

Screen id = `namespace + structural-hash`, where `namespace = (package, foreground-signal)`.

### Dump determinism + stabilization (default `compressed=False` — empirically pinned)

- **Pin the `compressed` flag — and pin it to the EXCLUDE-non-important setting.** Per the shipped
  uiautomator2 v3 README, the flag semantics are **counterintuitive**: `compressed=True` *includes*
  non-important nodes, and `compressed=False` (the library default) *excludes* them. We therefore
  default the STRUCTURAL HASH to **`compressed=False`** — the **fewer, semantic, important-for-a11y
  nodes only**. Including non-important layout-container/decorator nodes (`compressed=True`) adds
  render-to-render-variable nodes that are an **under-merge / volatility source**, contradicting the
  goal of a stable hash. ⚠️ This flag's meaning MUST be **empirically confirmed on-device before
  Spike 2** (dump the same screen both ways, diff node counts and classes) — the naming has caused
  repeated inversion; do not trust the assertion, verify it. Expose the other setting as a **knob**;
  pin whichever the on-device diff proves gives the stable semantic tree.
- **Set an explicit `max_depth`.**
- **Stabilization:** dump twice with a short delay; only record a node when two consecutive
  structural hashes match. If a screen never stabilizes (persistent animation → "could not get idle
  state"), mark it `volatile` and hash a **reduced subtree** under the activity/window namespace.
  (See §5.1 for how this reconciles with action-detection timing — they share one rolling snapshot.)
- **Strip volatile subtrees** before hashing via a resource-id allow/deny list: status-bar clock,
  battery, notification count/badges, media controls, carrier text. These are the top device-wide
  under-merge drivers.
- **`u2.dump_hierarchy` retries 3× with 1s delay** on transient empty results (library-acknowledged
  `HierarchyEmptyError`) — confirms empty/transient dumps are real.

### Under-merge vs over-merge + automatic mis-merge detection

Two failure modes, each fixable with per-context knobs:

- **Under-merge** (same screen → two hashes → state explosion): list content leaking into the hash,
  volatile subtrees, non-deterministic dumps, compressed-flag drift.
- **Over-merge** (two screens → one hash): dropping text/content-desc values, identical structural
  skeletons (ViewPager tabs, master/detail, wizard steps), generic container classes
  (`android.view.View`, `FrameLayout`, `ComposeView`) in launchers/Compose.

**Automatic mis-merge detection at record time** (turns "needs per-context tuning" from guesswork
into guided tuning, emitted to the NDJSON log for review):

- **Over-merge signal:** a recorded transition where `source hash == target hash` but a real input
  landed on a real element → suggest **promoting a discriminator** knob.
- **Under-merge signal:** revisiting a human-confirmed screen yields a new hash → suggest
  **demoting a volatile attribute**.

Persist the chosen knob set per package so replays are reproducible.

### Manual recorder makes this tractable (verdict: valid)

The dominant source of state explosion / under-merge in autonomous crawlers is the combinatorial
blow-up of visiting list items and dynamic content (the reason APE/Fastbot exist). A manual recorder
eliminates the explosion at the source: the human visits only real, reachable screens (tens, not
thousands), never enumerates every RecyclerView row. This converts "abstract a huge auto-explored
space correctly" into "merge a small set of human-visited screens." **Under-merge is cheap** (a
re-visit just re-confirms an existing node); **over-merge is the only serious residual risk** — so
the default fingerprint can be tuned conservatively toward over-merge tolerance.

> **Device-wide is NOT a costless win (correction).** Removing the app boundary removes the natural
> bound on the state space — every Settings sub-page, shade state, quick-settings panel, launcher
> page is a candidate node, and under-merge has no app boundary to contain it. The manual recorder
> is what keeps this finite; treat device-wide as a deliberate scope decision with a cost.

### Compose fingerprint profile (single-Activity Compose, no testTag)

Single-Activity Compose (the recommended Android architecture) defeats the activity namespace — the
resumed activity never changes across destinations — so the whole burden falls on the structural
hash exactly where Compose looks weakest. It is weaker, but **not** structureless, and v1 exploits
what is structurally guaranteed rather than depending on `testTag` cooperation OR on text/content-desc
being present.

**What actually appears in `dump_hierarchy` for Compose without `testTag`.** UiAutomator traverses
the **merged** `AccessibilityNodeInfo` tree. Two things are reliable, one is not:

- **Reliable: the node `class`.** className comes from semantics: `EditableText` →
  `android.widget.EditText`; otherwise `Role.toLegacyClassName()` → `Button` / `CheckBox` / `Switch` /
  `RadioButton` / `ImageView`; a `RangeInfo` node → `SeekBar`/`ProgressBar`; a node with **no role**
  defaults to **`android.view.View`**. App-cooperation-free structural signal.
- **Reliable: the merged tree is *sparser*.** Composables with no semantics emit **no node**; a
  clickable composable merges its descendant `Text` into a single node (text lands on the merged
  interactive ancestor, not a separate leaf). Build the per-node tuple over the merged tree.
- **NOT reliable: `text` / `content-desc` values.** `text` is frequently empty; `content-desc` is
  populated **only** when the dev set `Modifier.contentDescription`. Treat both as *opportunistic*
  signal that strengthens discrimination when present, never as a guaranteed field.

`resource-id` is empty without `testTag` + `testTagsAsResourceId` — exactly as in the View system
this profile already handles.

**The auto-applied Compose profile (v1).** When a screen is detected Compose-dominant, the pipeline
switches that namespace to a Compose-tuned knob set:

- **Keep the role-derived `class`** in every node tuple — `Button`/`CheckBox`/`Switch`/`EditText`/… vs
  the `android.view.View` default is the *primary* Compose discriminator and survives even when all
  text is dropped. The tree *shape* over these classNames carries the load.
- **Promote `content-desc`/`text` as presence-and-shape, defensively** — per node: has-text (bool),
  length-bucket, has-digits/has-alpha shape, same for content-desc; **never raw values** for ordinary
  nodes; explicitly tolerant of empty.
- **Promote the top-of-tree title/toolbar text by VALUE** — the one or few anchor nodes nearest the
  root of the `AndroidComposeView` subtree, as literal strings (the existing "promote title back IN"
  knob). Cheapest separator for wizard steps / tabs / master-detail that share a skeleton. If those
  anchors are empty, fall back to class-shape only and tag the screen `low`.
- **Keep `text` OUT of repeated list children** — list-collapsing + the manual-recorder bound (the
  human never enumerates `LazyColumn` rows) keeps promoted text from drowning the hash.

**Runtime detection (auto-applies, deterministically).** Detect Compose-dominance structurally from
the dump: locate `androidx.compose.ui.platform.AndroidComposeView` / `ComposeView` nodes; apply the
profile to the `(package, foreground-signal)` namespace when **(a)** ≥70% (default) of non-decor leaf
nodes descend from a Compose host, **and** **(b)** the resumed activity is unchanged across ≥2
recorded transitions in that namespace. Hybrid screens (small Compose island below threshold) keep the
default View profile. **Persist the resolved profile per namespace in `fingerprint_config`** (detection
runs only at record time; navigate-mode reuses the persisted choice).

The opt-in `Modifier.testTag` + `testTagsAsResourceId` path (Compose 1.2.0-alpha08+) remains supported
as an *upgrade* — when the app cooperates it surfaces stable `resource-id`s and lifts the screen to
`high` — but it is no longer the floor; the auto Compose profile is, with class-shape (not text) as its
guaranteed signal. Compose-detected screens are tagged `medium`, downgraded to `low` for single-Activity
Compose whose root-anchor title text is empty (hash rests on class-shape alone). The §7 over-merge
signal still fires to flag collisions at record time, and the Navigator widens arrival tolerance for
these nodes (§6, §13) so a residual over-merge degrades into a wider fuzzy match, not a false abort.

---

## 8. Selector Synthesis Ladder

**Ladder (priority order): content-desc / text → resource-id → coordinates.**

- **Do NOT rely on resource-id as primary** — Compose/Flutter commonly expose no
  `viewIdResourceName`. (Confirmed correct.)
- **Coordinates are LAST and NEVER the default** — raw touch-coordinate replay is brittle across
  resolutions and even across UI changes on the same device. An action whose only handle is
  coordinates is tagged `replayability: coordinate_only` (§4) and triggers a warning rather than a
  silent brittle replay.
- **`am start` / deep link** are transition shortcuts when *verified* available (§6), not a tap.
- **The selector ladder resolution lives in the library shim, not in generated code.** Each Action
  stores ONE canonical **structured** selector handle; the shim resolves it via the ladder at run
  time. u2 v3's `xpath` natively unifies `text`, `@resource-id` shorthand, and `content-desc` in one
  string and is the most robust single handle for the resolved form.

---

## 9. Hooks & Extensibility

### Two levels of actions

- **Level 1 — UI element actions** discovered from the hierarchy (the 5–30 tappable elements on a
  screen). **Recorded into the graph.**
- **Level 2 — system/device actions** (turn off screen, brightness, clear cache, Frida, mitmproxy,
  arbitrary `adb shell`). **NOT crawled** — they are **injectable hooks** the consumer attaches to a
  node/edge **in code, after the graph exists.** This is what keeps the graph **finite**.

### Hook model (verdict: valid)

Hooks are plain **Python callables attached BY REFERENCE** to nodes/edges of the loaded graph,
exactly LangGraph's `add_node(name, callable)` model — behavior lives in code keyed by id, never
embedded in the serialized graph.

- **Registry:** a dict keyed by screen-id / edge-id holding callables, with optional **decorator
  sugar** (`@graph.on_node(id)`, `@graph.on_edge(src, tgt)`) over that dict.
- **Phase tagging:** `crawl-time` (e.g. androguard decompile/manifest-parse on first `app` node) vs
  `nav-time` (Frida attach, HTTP intercept, AI-agent function).
- **Run slow device-side hooks OFF the loop** (`asyncio.to_thread` / worker) so a slow hook
  (decompile, Frida attach) never stalls the crawl recorder or the navigator.
- **Author in code, observe firing in the NDJSON log** (v1; a v2 dashboard reads the same events).
  Hooks are the **integration glue / consumer extension surface**, not the core IP (see §12
  defensibility). Make the **attach-point API** the ergonomic thing; the value is the model, not Frida
  itself.

### Why hooks are NOT serialized (verdict: valid — invariant)

Only **graph structure** is persisted to JSON; callables are **re-attached from code each run.**

- Serializing arbitrary callables (pickle/dill/cloudpickle) is **brittle** (non-portable across code
  versions) and an **arbitrary-code-execution security hole on load**.
- Keeping JSON to pure structure keeps the graph **diffable, reviewable, and secure.**
- This is documented as an **explicit invariant.**

---

## 10. Codegen & Output (DEFERRED FROM v1)

> **Cut from v1 (debate consensus).** The Navigator already replays any `from → to` path over the
> graph (§6); a generated Python module is a **strictly-dumber second execution path** that duplicates
> the Navigator, is premature optimization of an as-yet-unvalidated workflow, and is the **worst
> credential-leak sink** (baking `set_text` literals into `.py`). It is therefore **deferred behind the
> graph→emitter seam** (the `target=` pluggable-emitter interface) and **not built in v1**. v1 ships
> the library + Navigator; consumers call `navigate(graph, from, to)` directly. This section is the
> **design for the deferred emitter** so the seam stays well-defined; nothing here is v1 scope, and
> when it is built it MUST honor the §4 redaction invariant (only `{param: …}` handles, never secret
> literals, ever reach generated code).

### Output shape (deferred design)

A **flat, importable Python module of standalone path-functions**, e.g.:

```python
def home_to_payments(device):
    click(device, text="BanCoppel")
    wait_for_screen(device, "screen_id_xyz")
    ...
```

The genuinely novel bit is **graph-shortest-path-derived path functions** (any `from → to` via
shortest path, force_action-seeded), **not** record-transcript-to-script and **not** per-screen page
objects. Do not overclaim the function-body codegen itself as novel.

### (a) Jinja2 templating (risky → mitigated)

Jinja2 (3.1.6) is a generic string engine with **zero Python-syntax awareness** — raw output is
brittle. Pipeline:

1. **Jinja2** for the regular module skeleton (imports, header, function bodies are very regular).
2. **NEVER interpolate selector/literal values with bare `{{ value }}`.** Register an escaping
   filter. **Escaping rule (refinement):** use **`repr()` for general Python literals**; use
   **`json.dumps()` ONLY for strings.** They are **not** interchangeable — `json.dumps` emits
   `null`/`true`/`false`/`NaN`/`Infinity`, which are invalid or wrong in Python (`None`/`True`/`False`).
   For the design's actual payload (selector strings) `json.dumps` is fine, but never route a
   non-string literal (a `None` activity, a bool flag, a float duration) through it.
3. **Format as a mandatory post-step:** `ruff format` (or `black` 26.5.1). The template does not own
   indentation/line-length.
4. **Validity gate:** `compile(src, name, 'exec')` / `ast.parse` — fail generation if the module is
   not valid Python.

(`ast.unparse` (3.9+) or libCST are valid by-construction alternatives if template escaping becomes
painful; Jinja2 + repr-filter + ruff + ast-gate is the lower-effort v1 path.)

### (b) Generated code is THIN — calls the library shim, never u2 directly (valid; strongest lever)

Generated functions call **ONLY** the core library's stable public helper surface — `click`,
`long_click`, `swipe`, `scroll`, `send_keys`, `press`, `wait_for_screen`, `force_to`,
`verify_fingerprint` — **never u2 directly.** This isolates u2's real **2.x → 3.x break** (now
**3.5.2**) and all future churn to ONE place; the selector ladder and u2-version-specific calls live
behind the helpers. The shim maps onto stable u2 v3 primitives: `d.xpath(...).click()/click_exists(timeout=)/wait_gone(timeout=)`,
`d.press('home'|'back'|'recent')`, `d.app_start(pkg, activity, stop=True)`, `d.send_keys()`,
`d.swipe()`.

### (c) Selectors as named constants, not inline (risky → mitigated)

Emit a per-screen/per-action **`SELECTORS` constants block** (dict / dataclass instances) at module
top, keyed by screen id + action; transition functions **reference by name.** One edit point per
selector, readable regeneration diffs, single-selector human override. Store each selector as a
**structured handle** (`{text:…}` / `{xpath:…}`) resolved by the shim's ladder at run time — never
freeze coordinates or resource-id as the only handle.

### (d) Library-first / no-CLI (valid)

A CLI is not required: all consumers import Python, and v1 has no server (§3.1). Provide a **thin
programmatic entrypoint** — in v1 that is `navigate(graph, from, to)` plus `mapper.render(graph,
out_path)` for the offline static graph image. The deferred emitter entrypoint would be
`mapper.codegen(graph, out_path, target='python')`. (There is **no** `mapper.serve(...)` in v1 — the
live feed is v2.)

> **Nuance (correction):** the LangGraph/LangSmith analogy validates "author in code" but does
> **not** cover codegen — LangGraph users hand-write graph code, they do not run a generator.
> Codegen is an **addition beyond the analogy**, justified independently: it freezes a recorded path
> into a fast, dependency-light replay artifact.

### (e) Maestro dropped from v1 (valid)

Maestro is declarative YAML and **cannot host arbitrary Python nav-time hooks** (Frida/mitmproxy/adb/
AI-agent callables) — a core feature — so a Maestro artifact is strictly weaker for every v1
consumer. Design codegen as **`graph → pluggable emitter`** (template set chosen by `target`) so a
Maestro YAML backend slots in later at near-zero cost.

### Output hygiene + correctness gate (refinement)

- Standard machine-generated header on every module: `# Code generated by wendle from
  <graph>@<hash>. DO NOT EDIT.` Hand-customizations go in a **separate sibling module** that imports
  the generated one, so regeneration never clobbers edits.
- **`ast.parse` proves SYNTACTIC validity ONLY — not navigational correctness.** A module can pass
  the gate and still drive to the wrong screen, or call a helper with the wrong arity (e.g. after a
  u2 3.x signature change). **Add a replay smoke-test as the real correctness gate:** walk the
  generated path once against a device or a recorded-hierarchy fixture and `verify_fingerprint` at
  each step. `ast.parse` is demoted to a cheap pre-filter.

---

## 11. Proposed Stack

| Component | Choice | Notes / corrections |
|---|---|---|
| Device driver | **uiautomator2 v3** — pin `>=3.5,<4`, Python 3.8+ | v3 is a major rewrite (dropped atx-agent/jsonrpc). **Do NOT copy v2-era snippets.** Latest 3.5.2 (2026-05-28), actively maintained. Use `d.app_current()` (coarse signal), `d.xpath` (lxml-backed ladder). |
| XML parsing | **lxml** | backs u2 xpath. |
| Codegen | **Jinja2 3.1.6** + repr/json escaping filter | + `ruff format` (or `black` 26.5.1) + `ast.parse`/`compile` gate + **replay smoke-test**. |
| Static analysis / hooks | **androguard** (optional) | crawl-time: manifest parse for exported/BROWSABLE deep-link candidates; APK decompile hook. |
| Graph | **networkx** (pin 3.6.x) | `MultiDiGraph` + `multi_source_dijkstra`. Parallel-edge re-resolution is a **permanent** required step (networkx#7582 closed-as-intended). |
| Event sink (v1) | **append-only NDJSON file** + bounded-queue background writer | the entire v1 observability surface + the audit trail (§3.1, §3.4). No network. |
| Offline render (v1) | **networkx → graphviz** one-shot | static graph image from the finished log/graph. No browser, no live streaming. |
| Codegen | **DEFERRED from v1** (Jinja2 + repr/json filter + ruff + ast-gate + replay smoke-test) | behind the graph→emitter seam (§10); built later. Not v1 scope. |
| Server (v2) | **FastAPI** (deferred) | SSE read-only telemetry (HTTP/2) + WS mirror (own channel); localhost-bind + auth for any non-loopback (§3.4). Not built in v1. |
| Dashboard (v2) | web frontend (deferred) | observability-only live graph viz + device mirror. **Lowest moat, highest cost — deferred whole** (§14). |
| Screen mirror (v2) | **vanilla scrcpy 3.x** via ADB, WebCodecs decode (+ TinyH264/Broadway WASM fallback for Firefox-Android) | ~35–70ms latency claim is **unverified** — a v2 target to measure. **Do NOT depend on NetrisTV/ws-scrcpy** (stale, no auth). Not v1. |
| AccessibilityService | bundled, **opt-in**; **path ships v1.1** (protocol §5.x) | `canRetrieveWindowContent`, **no touch-exploration**; loopback-TCP + `adb forward` NDJSON protocol (§5.x). Real onboarding friction (Android 13+ Restricted setting; manual Settings toggle; Play Protect). **Enrichment only, not the trunk.** |
| Maestro | **out of v1** | future pluggable emitter only. |

**Screen mirror is entirely v2 (settled).** v1 has **no mirror** — observation is via the NDJSON log
(§3.1) + the offline static render. When the v2 dashboard adds a live mirror it is observability-only
and all control still lives in code (§2, §3.3), so no control path crosses the mirror channel — mirror
latency cannot affect navigation correctness at any frame rate; it only governs how "live" an
observer's view feels. The v2 floor is a periodic JPEG push at ~2–5 fps on its own independent channel
(u2 v3 returns raw JPEG via `d.screenshot(format='raw')`, forwarded as binary WS frames, trivially
within capacity); real-time scrcpy 3.x + WebCodecs (TinyH264/Broadway WASM fallback for
Firefox-Android) is the v2 stretch, its ~35–70 ms figure a **v2 target to measure**, never a v1 risk.

---

## 12. Known limitations & positioning

All per-decision mitigations live inline in their respective sections (§3/§5/§6/§7/§9/§10/§11); this section records only the irreducible scope facts that no mitigation removes, plus prior-art positioning.

**Known limitations**

- **Coordinate-only screens.** Screens with no stable selector (testTag-less Compose, WebView, custom-rendered/game surfaces) are recordable but replay falls back to raw `coordinate_only` taps, which are device-resolution-bound and not reliably replayable across layout changes.
- **WebView = low fingerprint confidence.** WebView subtrees expose unstable, content-driven hierarchies; their structural signatures are inherently low-confidence and are flagged as such rather than treated as authoritative.
- **Flutter & games out of v1 scope.** Flutter's single rendered surface and game engines expose no usable uiautomator2 hierarchy; both are out of scope for the v1 hierarchy-only baseline.
- **Device-scoped graph; no fleet portability in v1.** A recorded graph is bound to one device's calibration, resolution, OEM skin, and OS build. **What ports vs what doesn't:** semantic selector edges (text / content-desc / resource-id) are resolution-independent and survive across devices; the **structural fingerprint hashes, `coordinate_only` edges, and `force_actions` do NOT port** (per-OEM tree differences, pixel-bound coordinates, per-device activity/deep-link availability). So a single graph cannot run unchanged on a mismatched fleet in v1. **v2+ portability design (logical graph vs device overlay):** split the graph into (a) a **portable logical layer** — nodes = semantic screens, edges = semantic selectors — recorded once, and (b) a **per-device-class overlay** — fingerprints, coordinates, `force_actions`, calibration. Group fleet devices into **device-classes** by `(OEM, OS version, app version, resolution)` and regenerate each class's overlay with a lightweight automated **re-fingerprint/re-calibrate pass** (cheaper than a full manual re-record). One logical graph, N overlays. This split is **not built in v1** and is the prerequisite for any cross-fleet RPA claim.
- **Maintenance is reduced, not eliminated.** The structural fingerprint depends on UI node classes + hierarchy, so an app update that changes layout invalidates the affected screens' fingerprints and requires **re-recording those screens** (selector edges often survive; hashes don't). This is genuine ongoing maintenance — the honest claim is that re-recording a changed flow is typically cheaper than re-authoring the equivalent selector code, and that Maestro/Appium flows break on the same app changes; it is **not** "zero maintenance."
- **Single-Activity testTag-less Compose is the fingerprint correctness ceiling.** After the §7 Compose-profile resolution (promote content-desc/text shape + toolbar title by value), structurally-and-textually-identical Compose destinations can still over-merge; this flagged low-confidence residual, absorbed by the Navigator's widened arrival tolerance, is the hard ceiling of hierarchy-only fingerprinting.
- **Low-confidence tap bindings need human re-confirmation.** Per §5.1, a tap during a transition guard interval (or with no snapshot validity window) is flagged for re-confirmation via the code-side `confirm_edge`/`correct_edge`/`reject_edge` API (§3.3) rather than recorded as a guessed selector — fast multi-step interaction therefore requires occasional human disambiguation, not silent best-guessing.
- **No live observation in v1.** v1 has no dashboard and no screen mirror — observation is the NDJSON log + an offline static graph render (§3.1). Live observability (and a screen mirror, which even in v2 stutters at ~2–5 fps JPEG and is cosmetic only) is deferred to v2. This is a deliberate scope cut, not a capability gap in record/replay.

**Prior-art & positioning**

The occupied gap is a four-axis intersection — manual human recording + device-wide (whole-phone) scope + per-node/per-edge code hooks + an observability-only dashboard, compiled to a graph-shortest-path Python module — and no surveyed system sits at that intersection. Adjacent prior art solves orthogonal problems: RIDA does cross-app record/replay; SARA does Frida-instrumented record/replay; RERAN does low-level event-stream replay; DroidBot and Fastbot do autonomous exploration; SkillDroid (arXiv 2604.14872) and AppAgentX (arXiv 2503.02268) do LLM-driven autonomous control; Delm (TOSEM 2024), Mosaic, and MobiPlay address replay fidelity and cross-device translation but not authored device-wide navigation. None overlap the four-axis target. The durable differentiation is therefore device-wide scope + instrumentation-free capture (getevent + uiautomator2 hierarchy dump, no Frida/method-hooking) + code as the authoring surface. Note that the state-machine abstraction technique itself is published prior art and is not claimed as invented here; the contribution is the unoccupied combination, not the abstraction.

---

## 13. App-Type Coverage Matrix

UIAutomator sees **ONE merged accessibility tree regardless of framework**, so the same dump
mechanism applies device-wide; the selector ladder + fingerprint adapt per-screen. Coverage and
**per-screen `fingerprint_confidence`**:

| App type | Hierarchy / selectors | Fingerprint confidence | Replayability | v1 status |
|---|---|---|---|---|
| **View-based** | Rich resource-ids, text, content-desc | **high** | high | Best supported. |
| **Jetpack Compose** | Visible tree; lean on text/content-desc; usually **no** resource-id; generic container classes; single-Activity → no activity disambiguation | **medium** (low for single-Activity Compose without testTag) | medium | Supported; weakest structural signal. testTag/`testTagsAsResourceId` knob improves it (needs app cooperation). |
| **Hybrid (View+Compose+WebView)** | One merged tree; ladder adapts per-screen | mixed (per sub-screen) | mixed | The common case — fine; confidence varies by sub-screen. |
| **WebView** | DOM partially/flakily exposed; dumps often show only the container with no children unless a11y manager was enabled at render time | **low** | low | **Low-confidence v1.** Wider arrival tolerance; may be recordable-not-replayable. |
| **Flutter** | Opaque single `FlutterView`; no usable tree | **out_of_scope** | n/a | **Out of scope v1.** Deferred to a vision/OCR version. |
| **Games** | Opaque render surface | **out_of_scope** | n/a | **Out of scope v1.** |

The Navigator uses `fingerprint_confidence` to widen its arrival match tolerance for medium/low
nodes and to fail-safe stop-and-report rather than misclassify. WebView low-confidence and Flutter/
games out-of-scope are **hard gates in the data model**, not silent unstable hashes.

---

## 14. Build Sequence Recommendation

**v1 = the headless core only** (record → fingerprint → navigate → NDJSON log). Codegen, the live
server, and the dashboard are **cut** (§2). A cross-cutting requirement runs through every spike:
define a **`DeviceDriver` port** and make the fingerprint (§7), gesture-segmentation (§5), and
selector ladder (§8) **pure functions over driver outputs** (XML-in → hash-out), so they run in CI
against recorded-hierarchy **fixtures — including adversarial ones** (truncated trees, empty dumps,
jittered latency) — without a live device. Spike in this order, hardest-first, and **gate the whole
project on the Spike 1 kill criterion** before funding Spikes 2+:

### Spike 0 — Device calibration, input-node discovery & timebase validation (de-risks blockers #1, #2)
Build the per-device onboarding: `getevent -lp` → discover the touchscreen node (probe
`ABS_MT_TOUCH_MAJOR`) → read `ABS_MT_POSITION_X/Y` max → scale raw→pixel → cross-check against u2
display size → persist `device_profile`. **Also validate the timebase (§5.1):** emit a known synthetic
input via `sendevent` at a host-known instant and confirm `getevent -lt` reports it in the expected
`CLOCK_MONOTONIC` frame — per device, since OEM getevent builds vary. **Acceptance:** a tap at a known
element's center, read from getevent and scaled, lands within that element's bounds, AND the timebase
cross-check passes, on **≥2 physically different devices — one AOSP-clean (e.g. Pixel) and one
OEM-skinned (Samsung/Xiaomi)**, not one calibrated Pixel.

### Spike 1 — getevent capture + ring-buffer correlation + KILL GATE (THE riskiest; de-risks #3, #4, §5.1)
Stream getevent, segment tap/long-press/swipe via `BTN_TOUCH`/`SYN_REPORT`/`ABS_MT_SLOT`, and bind
each tap to the correct hierarchy via the **timestamp-window ring buffer** (§5.1), emitting LOW-confidence
taps as `needs_confirmation` edges. Include `set_text` via EditText pre/post text-diff **with
redaction-by-default** (§4 — password fields never store a literal). **This is a multi-week subsystem,
not a quick spike, and it is the project's existential risk — it carries an explicit numeric KILL
GATE.** Measure at **realistic sustained tapping speed** on a **battery-optimization-ON OEM device**
(not an idealized Pixel): report selector-recovery %, the `needs_confirmation` flag rate, p50/p95/p99
`dump_hierarchy` latency, and the silent-truncation/empty-dump rate. **Kill criterion (set the numbers
before building, e.g. ≥85% HIGH-confidence selector recovery and p95 dump < 1.5 s on the OEM device):
if unmet, the capture bet has failed — stop, do not fund Spikes 2+.** Multi-finger gestures must be
flagged, not mis-recorded.

> **✅ CLEARED (on-device, two OEMs, both protocols).** Measured 2026-05-31 via `scripts/spike1_gate.py`:
> **ZTE (btn_touch): 96.5% recovery, 1.8% coordinate-only, 57 taps, p95 0.46 s; Samsung (type_b): 98.1%
> recovery, 1.9% coordinate-only, 54 taps, p95 0.29 s.** Both clear the ≥85% bar (recovery defined as
> ANY stable selector — text/content-desc/resource-id, since resource-id is locale-robust; see the
> metric change below), needs-confirmation ~0–2%, zero empty dumps. Selector recovery on unlabeled
> clickable containers relied on **descendant-label borrowing** (selectors.py — the "clickable region
> containing text X" pattern). The capture bet is validated; Spikes 2+ are funded.
>
> **Carry-forward to Spike 3 (replay robustness):** borrowed/text selectors sometimes capture *volatile*
> content (view counts, timestamps, full note bodies). They count as recovery but are brittle to replay
> — the Navigator must prefer stable handles (resource-id) and detect/avoid volatile borrowed text.
>
> **◑ Capture-fidelity refinements (on-device, 2026-06-01, driving real flows in Spike 3).** Live record→
> replay surfaced and FIXED Spike-1-layer capture defects the gate harness could not: tap-time binding
> (stale arrival snapshot), borrow-skips-nested-control, tap-vs-swipe in DISPLAY pixels, dropped-navigation
> reconcile, volatile-keyed-by-structure, and a settle `max_wait` cap so the recorder keeps up. These live
> in `record/session.py` / `capture/`.
>
> **✅ TEXT ENTRY wired into the live recorder (2026-06-01; device-free + 3 adversarial security rounds).**
> Designed via a `map→design(judge panel)→verify→blueprint` workflow (it killed two FATAL design flaws up
> front). Implementation: the live refresher runs a text-entry FSM (tracks the focused `EditText` per
> cycle, finalizes each field on switch/blur via `detect_text_entry`); `record_gesture` SUPPRESSES taps
> inside the soft-keyboard REGION (`_ime_bounds` = bbox of the `android.inputmethodservice.*` window —
> region-based so it catches modern Gboard's generic-`View` keys, and falls back to `current_snapshot` so
> there's no post-navigation hole) and attaches the set_texts (one per login field) to the submit edge as
> **`Transition.pre_actions`** — the fix for the FATAL "a set_text on `Screen.actions` is never replayed."
> Passwords stay redacted to `{param}`; keystroke suppression is itself the redaction control. Replay:
> atomic `set_text` by default; **per-key** (`driver.type_text`, via u2 `send_keys` — NOT a shell string,
> after a command-injection fix) only for reactive fields (auto-detected by app-window churn) or a
> per-field `Navigator(replay_modes=...)` override. **Three impl-review rounds** found + fixed real
> password-leak paths (resource-id-only IME detection, generic-class keys, the post-nav window) and
> multi-field/ordering bugs. **Known limit:** a custom in-app keypad with NO IME markers (no
> `inputmethodservice` node) is undetectable → its taps are not suppressed (rare; documented). On-device
> login/search recording is the remaining validation.
>
> **✅ STATE-SETTING ACTION FAMILY generalized (2026-06-01; reference-grounded + verified).** A
> `map→design→verify→blueprint` workflow grounded the model on **Playwright `setChecked()`** + **DroidBot
> `SelectEvent`** + the Appium `if(!isSelected())click()` idiom: a toggle/checkbox/switch/radio is an
> **idempotent value-carrying `pre_action`** (`set_checked`, `value={checked: bool}`), the SAME shape as
> `set_text` — NOT a node, edge, or node-state. `UINode` gained `checkable/checked/selected` (parsed
> out-of-band); `detect_checkable_entry` matches the tapped widget across before/after by resource-id and
> records the DESIRED boolean; `record_gesture`'s effectiveness filter promotes a real flip to `_pending`
> (rides the submit edge) instead of an `intra_actions` probe; the navigator replays via idempotent
> `driver.set_checked` (read-modify-verify — flips only on mismatch, safe against a pre-checked launch).
> **Load-bearing decision: the fingerprint STAYS BLIND to `checked`** — folding it in mints 2^N nodes per N
> checkboxes; every *replay* tool (Playwright/Selenium/Appium/Espresso) agrees, only autonomous *explorers*
> (DroidBot full state_str, Stoat, MobiGUITAR, APE) fold it in *because* they want the state explosion. Two
> impl-review rounds fixed: capture-by-resource-id (the cross-snapshot diff), selector-not-bound-to-the-
> state-label, `selected` flips for tabs, and read-modify-VERIFY. Sliders/`set_progress` DEFERRED (no
> reliable settable progress via UIAutomator). Honest asymmetry: a toggle with no following navigating edge
> → `uncommitted_state` marker, not replayed (sibling of `uncommitted_text`).

### Spike 2 — Device-wide structural fingerprint pipeline, behind the DeviceDriver port (§7)
**First empirically pin the `compressed` flag on-device** (dump the same screen both ways, diff node
counts/classes) — do not trust the §7 assertion. Then implement the configurable pipeline as **pure
functions over the `DeviceDriver` port** (`compressed=False` default, fixed `max_depth`, list-collapse,
volatile-subtree denylist, double-dump stabilization, dumpsys-activity + dumpsys-window namespace,
`app_current()` try/except) plus automatic mis-merge detection. Build a **recorded-hierarchy fixture
corpus including adversarial fixtures** (truncated/empty dumps, per-OEM dumpsys formats) so the
pipeline is CI-testable without a device. **Acceptance:** across home/launcher, SystemUI shade,
Settings (multi-level), and one View-based + one single-Activity Compose app, the same human-revisited
screen hashes identically (no under-merge) and two genuinely different screens do not collide (no
over-merge), with mis-merge signals firing where expected — **all reproducible against the fixture
corpus in CI**, and the over-merge detector proven firing on a real Compose app.

> **◑ PARTIALLY CLEARED (on-device, one device, with revisits).** Validated 2026-05-31 via
> `scripts/spike2_capture_corpus.py` + `spike2_corpus_check.py` on Samsung Android 16: a 20-sample
> corpus over ~14 distinct screens across diverse real apps (WhatsApp, YouTube, Spotify, Gemini
> streaming, Calendar, Tasks, Sheets) + launcher **PASSED** — every revisited screen hashed identically
> (no under-merge), distinct screens stayed distinct (no over-merge), every sample's re-dumps agreed
> (stable). The corpus is the **standing regression gate** (`tests/test_corpus.py`, runs locally when
> `corpus/` is present; gitignored as device PII). The path to PASS surfaced and fixed real bugs the
> static design missed — capture-timing (settle), IME/SystemUI overlay churn (sanitizer), per-dump
> Compose-profile flicker (resolve-once), list/pager/scroll mis-collapse, and launcher page jitter
> (namespace-dominant for the home anchor).
>
> **HONEST GAPS still open (do NOT call Spike 2 fully done):**
> - **Compose unexercised** — the corpus had **0 Compose-dominant samples**; the §7 single-Activity
>   testTag-less Compose correctness ceiling and "over-merge detector on a real Compose app" remain
>   **unvalidated**. Capture a Compose-heavy app before relying on Compose.
> - **One device only** — Samsung; no cross-OEM / cross-version corpus yet.
> - **Live/never-settle screens** — many dynamic screens never reached settle and were skipped; the
>   `volatile → namespace-dominant + reduced-subtree` fallback is **designed but not built** (Spike 3).
> - **APE-inspired mis-merge refinement** — deferred to Spike 3 (needs recorded transitions; the
>   `Transition.settled` / `landed_on_real_element` seam ships now).

### Spike 3 — Graph + Navigator replay (de-risks #5, #6, #7, §6)
Build `MultiDiGraph` persistence (JSON, structure-only), `multi_source_dijkstra` nearest-anchor
routing with weighted edges + parallel-edge re-resolution, verified-at-record-time force_action
(androguard manifest mining + fingerprint verification), and the wait-then-verify-with-tolerance
arrival loop + watcher set (with the **shared dump lock** mitigating the contention race).
**Acceptance:** replay a recorded `home → deep-app-screen` path on a real app, surviving a runtime
permission dialog and a loading spinner, with anchor + per-step fingerprint verification and a clean
stop-and-report when deliberately knocked off-graph.

> **◑ PARTIALLY CLEARED — re-grounded on DroidBot's UTG (device-free + adversarial review; on-device
> acceptance pending).** Built 2026-06-01. After an initial pass accumulated per-case patches (swipe
> penalty, self-edge handling, namespace-degrade verify, anchors-as-teleports), Spike 3 was **re-grounded
> on DroidBot's UI Transition Graph** (`honeynet/droidbot`, ICSE-C'17) — the closest hierarchy-only,
> networkx-backed twin with an explicit navigate-to-state routine. Four principles adopted, each
> subsuming a patch:
> - **Two-level state identity** — `structure_id()`, a text-free skeleton hash distinct from the EXACT
>   `fingerprint()` (which folds text in for Compose). Promotes the old namespace-degrade *verify* hack
>   and the `V`+ns volatile node into a first-class **STRUCTURE routing/verify tier**.
> - **Effectiveness filter at record** — an action that doesn't change the EXACT fingerprint is not an
>   edge (becomes a `reveal`/`probe` intra-action). **Subsumes the swipe-penalty + self-edge patches.**
> - **Closed-loop navigation** — `navigate()` re-observes and re-plans every step (never an open-loop
>   walk), seeded only from `{actual}` or target-package anchors, bounded by `STEP_CAP` + a two-observation
>   progress debounce + a wrong-package gate with bounded restart-as-recovery. **Subsumes anchors-as-teleports.**
> - **Graded `verify_match` → Tier** (EXACT > STRUCTURE > WEAK > UNVERIFIABLE > MISMATCH): a structure-only
>   match on an adapter-list-dominant screen, an ambiguous structural twin, or a no-probe namespace hit is
>   **UNVERIFIABLE → honest `arrived_unverified`/stop, never a confident wrong "arrived."** Fixed the prior
>   `verify.py` false-positive where a missing probe returned True.
>
> **Validation so far:** 215 device-free tests (FakeDriver) green; a fan-out **adversarial code review**
> (`spike3-adversarial-review` workflow) found 6 real defects — incl. a high-severity confident-false-arrival
> on structural twins resolved by graph insertion order — **all fixed and regression-locked** (commit
> `f4842ed`), then re-verified by a second focused workflow.
>
> **◑ ON-DEVICE: Case 1 PASSED (2026-06-01, Galaxy S23 / SM-S918U).** Full record→fingerprint→graph→
> replay→**EXACT arrival** on a single-Activity-siblings app (Settings: Wi-Fi vs Bluetooth, both
> `com.android.settings/.SubSettings`, distinguished by exact fingerprint, no cross-arrival). The run
> surfaced and FIXED two real **capture-side** defects that device-free tests could not have caught:
>   - **stale arrival snapshot** — taps bound to the hierarchy captured on arrival, but a collapsing
>     toolbar / scroll moved the layout before the tap (a "Connections" tap recorded as the title
>     "Ajustes"). Fixed: `RecordSession(live_refresh=True)` binds each tap to a fresh tap-time dump.
>   - **borrow-from-nested-control** — a Wi-Fi *row* tap recorded the nested Wi-Fi *toggle*'s
>     content-desc, so replay toggled Wi-Fi instead of opening it. Fixed: `borrow_descendant_selector`
>     skips independently-clickable children and prefers the row's text label.
>   Before these fixes the navigator behaved CORRECTLY (honest `off_graph`, never a false arrival) — the
>   failures were upstream capture quality, not Spike 3.
>
> **◑ ON-DEVICE: Instagram drive SUCCEEDED (2026-06-01, same device) — the hardest app.** Recorded and
> then drove `Instagram home → Messages → Robertín's chat` via `scripts/spike3_replay.py --trace`. This
> app stacks EVERY hard property (single-Activity: feed/inbox/chats all `MainTabActivity`; dynamic feed;
> modals; volatile labels) and forced out a chain of real **capture-side** fixes, each earned live:
>   - **anchor-trust** (navigate) — a just-`am_start`-forced anchor is trusted by namespace, so a dynamic
>     home whose skeleton won't reproduce stops relaunching the app `MAX_RESTARTS` times.
>   - **tap-vs-swipe in DISPLAY pixels** (capture) — the swipe threshold was in RAW panel units; on a
>     4095-panel/1440-display that made ordinary taps register as swipes. Now scaled per-axis.
>   - **dropped-navigation reconcile** (capture) — a dropped navigating tap no longer fabricates a bogus
>     direct edge; the recorder materializes the real current screen (element-overlap distinguishes a
>     collapse/scroll from a navigation) and flags the missing hop as `implicit_screen_change`.
>   - **volatile keyed by structure** (capture) — a never-settle screen's id was namespace-only, so every
>     dynamic screen of one Activity collapsed into ONE node; now keyed by text-free `structure_id`.
>   - **settle `max_wait` cap** (capture) — the 5 s default stalled the recorder on every never-settling
>     screen so it fell behind fast navigation and skipped/mis-stitched screens; capped at 1.6 s.
>   - **trace-replay + stable-label fallback** (replay) — `--trace` walks the RECORDED edge order (so a
>     "self-loop" that only exists because two screens merged is still executed), and a decayed label
>     (`'Robertín, Enviado hace 53 min'`) re-resolves on its stable leading segment (`'Robertín'`).
>
> **Verdict: Spike 3 (graph + navigator + replay) is functionally COMPLETE and proven on real hardware**
> for tap/swipe navigation, including `set_text` REPLAY (the navigator's `_execute`). The single-Activity
> over-merge I first feared is NOT a wall — distinct dynamic sub-screens ARE distinguishable by structure;
> Instagram's home/inbox merge is the narrower case of "two scroll-collapsed lists under shared chrome,"
> handled at replay by `--trace`.
>
> **HONEST GAPS still open:**
> - **TEXT ENTRY is a Spike-1 (capture) follow-up, NOT Spike 3.** Recording "search + send" captured each
>   keystroke as a per-key tap because `capture/text_entry.py::detect_text_entry` (built + unit-tested in
>   Spike 1) is **not wired into the live `RecordSession` loop**. NEXT SPIKE-1 TASK: on `EditText` focus,
>   diff field text via the live refresher → emit one `set_text` (redacting passwords) and **suppress the
>   keystroke taps**. The navigator already REPLAYS `set_text`, so only the record side is missing.
> - The other 5 acceptance cases are not yet run on-device (dynamic home/restart-resume/adapter-list/
>   content_drift/cross-app).
> - **Record from the launcher** (tap the app icon) so the app earns its `am_start` anchor — starting
>   inside the app or on the notification shade yields `anchors: []` (must inject manually).
> - **Two scroll-collapsed list screens under identical chrome** (Instagram home vs inbox) still merge to
>   one node — list scroll-invariance erases the discriminator. Driveable via `--trace`; a content-aware
>   list discriminator is a deferred Spike-2 refinement.
> - **Compose/adapter-list twins** within one Activity are honestly *unverifiable*, not navigable (by
>   design, no content discrimination in v1); **edge success-probability** deferred (manual record = M≈1).

### Spike 4 — Observability: NDJSON event log + offline render (closes v1, de-risks §3)
Emit the versioned event envelope (§3.5) **append-only to an NDJSON file** via a bounded-queue
background writer (drop-oldest, never stall the recorder; fsync on pause/close), with the §4 redaction
invariant enforced on every line and the privileged-hook capability gate writing audit entries (§3.4).
Add the one-shot **offline `networkx → graphviz`** renderer for a static graph image. **Acceptance:**
a full record→navigate session produces a complete, **redacted** NDJSON log + a correct static graph
render; a deliberately slow disk writer never stalls the recorder. **This closes v1.**

### Deferred beyond v1 (do NOT pull forward)
- **Codegen** (§10) — behind the graph→emitter seam, built only after the workflow is validated.
- **Live server + dashboard** (FastAPI SSE/WS feed; observability-only web UI) + **screen mirror** —
  **v2**. They read the same §3.5 envelope; the NDJSON log is their on-disk precursor.
- **AccessibilityService enrichment** (§5.x) — **v1.1**; protocol already specified, security contract
  bound (§3.4).

> **Sequencing rationale:** Spikes 0→1 attack the single most likely project-killer (action capture),
> and **Spike 1 carries a hard numeric kill gate** — if it fails on real OEM hardware, the project
> stops there. Spike 2 attacks the core hard problem (fingerprint) behind a testable DeviceDriver port.
> These three plus Spike 3 (Navigator) are the genuinely novel/hard contribution and the only durable
> moat (§12); Spike 4 is the thin NDJSON log that closes v1. **Gate-zero deliverable (before Spike 0)
> — SATISFIED:** the persona is written (§1 "Who it's for") — developers who need cross-app/cross-device
> Android automation without hand-building each flow (Maestro/UIAutomator) or babysitting an LLM
> screenshot loop, focusing on their top layer (Frida/agents/scraping/RPA). Abuse surface is bounded by
> redaction-by-default (§4) + the privileged-hook capability gate + audit log (§3.4). Everything cut to
> v2 is supporting surface — do not let it pull scope forward.
