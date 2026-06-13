"""wendle — the stable command-line front for the library's verbs.

A THIN shell: parse args -> call the library -> print a redaction-safe report -> exit.
No automation logic lives here; waits, selectors, launch, and verification are the
library's job. The CLI's one design decision is making the honesty contract
shell-visible through exit codes:

    0  verified success      (replay completed / navigate arrived)
    1  crash                 (an uncaught exception — Python's own exit code)
    2  usage error           (bad flags, missing file, unknown node, bad hooks file)
    3  HONEST STOP / refusal (stopped / arrived_unverified / off_graph / content_drift
                              / cross_app_boundary / force_failed / no_route)

3 is deliberately distinct from 1: a refusal is the framework working as designed —
it would not guess — and scripts must be able to branch on that.

Redaction: `--param name=value` injects a credential for a recorded sensitive field;
the value reaches the device and is NEVER echoed to stdout/stderr. Live step and
result lines reuse the library's value-free reprs.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from typing import Dict, List, Optional

from wendle import __version__
from wendle.graph import Graph, StaleRecordingError
from wendle.navigate.navigator import navigate
from wendle.record import record
from wendle.render import render
from wendle.replay.engine import replay_recording
from wendle.replay.hooks import HookRegistry

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_HONEST_STOP = 3


class _UsageError(Exception):
    """A caller mistake (not a device/honesty outcome): report and exit 2."""


def _make_driver(serial: Optional[str]):
    # The single seam where a real device enters the CLI (tests replace this).
    from wendle.driver.u2_driver import U2Driver  # lazy: no device import unless needed

    return U2Driver(serial)


def _load_graph(path: str) -> Graph:
    try:
        with open(path) as f:
            return Graph.from_json(f.read())
    except FileNotFoundError:
        raise _UsageError(f"recording not found: {path}")
    except IsADirectoryError:
        raise _UsageError(f"recording is a directory: {path}")
    except ValueError:
        raise _UsageError(f"recording is not a valid graph JSON: {path}")


def _parse_params(pairs: List[str]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for pair in pairs:
        key, eq, value = pair.partition("=")
        if not eq or not key:
            # never echo the malformed token — it may BE the credential, typo'd
            raise _UsageError("--param expects k=v (malformed pair given; value not shown)")
        params[key] = value
    return params


def _load_hooks(path: str) -> HookRegistry:
    if not os.path.isfile(path):
        raise _UsageError(f"hooks file not found: {path}")
    spec = importlib.util.spec_from_file_location("wendle_hooks", path)
    if spec is None or spec.loader is None:
        raise _UsageError(f"cannot import hooks file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # the developer's code; their exceptions = real tracebacks
    candidate = getattr(module, "hooks", None)
    if isinstance(candidate, HookRegistry):
        return candidate
    registries = [v for v in vars(module).values() if isinstance(v, HookRegistry)]
    if len(registries) == 1:
        return registries[0]
    raise _UsageError(
        f"{path} must define exactly one HookRegistry (e.g. `hooks = HookRegistry()`)")


# ---- honesty -> exit-code mapping (the contract table) ----

def _exit_for_replay(result) -> int:
    return EXIT_OK if result.ok else EXIT_HONEST_STOP


def _exit_for_nav(outcome) -> int:
    # only a CONFIRMED arrival is shell success; "plausibly there" is a refusal
    return EXIT_OK if outcome.status == "arrived" else EXIT_HONEST_STOP


# ---- subcommands ----

def _cmd_record(a) -> int:
    driver = _make_driver(a.serial)
    print(f"recording for {a.duration:.0f}s — walk the app by hand, pause on each screen")
    graph = record(
        driver=driver, duration=a.duration, out=a.out, serial=a.serial,
        # value-free live progress: action type + target node only, never a selector value
        on_transition=lambda t: print(f"  + {t.action.action_type} -> {t.target}"),
    )
    n_screens = len(graph.g.nodes)
    n_edges = sum(1 for _ in graph.ordered_transitions())
    print(f"recorded {n_screens} screen(s), {n_edges} transition(s) -> {a.out}")
    return EXIT_OK


def _cmd_replay(a) -> int:
    graph = _load_graph(a.recording)
    from wendle.graph import check_signature_version
    check_signature_version(graph)  # stale recording: refuse BEFORE any device work
    params = _parse_params(a.param)
    hooks = _load_hooks(a.hooks) if a.hooks else None
    driver = _make_driver(a.serial)  # after validation: no device touched on a usage error
    print(f"replaying {a.recording}")
    result = replay_recording(graph, driver, params=params, hooks=hooks,
                              on_step=lambda s: print(f"  {s!r}"))
    print(f"RESULT: {result.status}")
    if result.failed_step is not None:
        print(f"  stopped at {result.failed_step!r}")
        if result.failed_step.error:
            print(f"  reason: {result.failed_step.error}")
        print("  (an honest stop — the framework refused to guess past the named step)")
    return _exit_for_replay(result)


def _cmd_navigate(a) -> int:
    graph = _load_graph(a.recording)
    from wendle.graph import check_signature_version
    check_signature_version(graph)  # stale recording: refuse BEFORE any device work
    node_ids = set(graph.g.nodes)
    if a.to not in node_ids:
        raise _UsageError(
            f"unknown target node {a.to!r} — run `wendle nodes {a.recording}` to list ids")
    from_id = a.from_
    if from_id is None:
        anchors = graph.anchors()
        if len(anchors) != 1:
            raise _UsageError(
                f"--from is required: the recording has {len(anchors)} verified anchors "
                f"(run `wendle nodes {a.recording}`)")
        from_id = anchors[0]
    elif from_id not in node_ids:
        raise _UsageError(
            f"unknown --from node {from_id!r} — run `wendle nodes {a.recording}` to list ids")
    params = _parse_params(a.param)
    driver = _make_driver(a.serial)
    outcome = navigate(graph, from_id, a.to, driver, params=params)
    detail = f" — {outcome.detail}" if getattr(outcome, "detail", None) else ""
    print(f"NAVIGATE: {outcome.status}{detail}")
    if outcome.status != "arrived":
        print("  (an honest outcome — the navigator does not pretend arrival)")
    return _exit_for_nav(outcome)


def _cmd_nodes(a) -> int:
    graph = _load_graph(a.recording)
    anchors = set(graph.anchors())
    for sid in graph.g.nodes:
        screen = graph.screen(sid)
        mark = "  [anchor]" if sid in anchors else ""
        print(f"{sid}  {screen.namespace}{mark}")
    return EXIT_OK


def _cmd_render(a) -> int:
    graph = _load_graph(a.recording)
    out = a.out or os.path.splitext(a.recording)[0] + f".{a.target}"
    # the recording path is threaded as DATA to self-referencing codegen targets (the Python nav
    # module loads the map it was built from); value-free targets ignore it.
    path = render(graph, out, target=a.target, recording_path=a.recording)
    print(f"wrote {path}")
    return EXIT_OK


# ---- parser / entry ----

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wendle",
        description="record / replay / navigate any Android app, honesty-first. "
                    "Exit codes: 0 verified success, 2 usage, 3 honest stop/refusal.")
    p.add_argument("--version", action="version", version=f"wendle {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="walk an app by hand on the device; save a navigable map")
    rec.add_argument("--out", required=True, help="where to save the recording (graph JSON)")
    rec.add_argument("--duration", type=float, default=120.0, help="capture window in seconds")
    rec.add_argument("--serial", default=None, help="adb device serial (default: the only device)")
    rec.set_defaults(func=_cmd_record)

    rep = sub.add_parser("replay", help="faithfully re-enact a recording on the device")
    rep.add_argument("recording", help="recording (graph JSON) to replay")
    rep.add_argument("--param", action="append", default=[], metavar="k=v",
                     help="credential for a recorded sensitive field; never logged")
    rep.add_argument("--hooks", default=None, metavar="FILE.py",
                     help="python file defining a HookRegistry to run between steps")
    rep.add_argument("--serial", default=None)
    rep.set_defaults(func=_cmd_replay)

    nav = sub.add_parser("navigate", help="route to a node of the map, verifying arrival")
    nav.add_argument("recording")
    nav.add_argument("--to", required=True, help="target node id (see `wendle nodes`)")
    nav.add_argument("--from", dest="from_", default=None,
                     help="start node id (default: the sole verified anchor)")
    nav.add_argument("--param", action="append", default=[], metavar="k=v")
    nav.add_argument("--serial", default=None)
    nav.set_defaults(func=_cmd_navigate)

    nod = sub.add_parser("nodes", help="list the map's node ids (anchors marked)")
    nod.add_argument("recording")
    nod.set_defaults(func=_cmd_nodes)

    ren = sub.add_parser("render", help="write an offline, redaction-safe map/outline")
    ren.add_argument("recording")
    ren.add_argument("-o", "--out", default=None, help="output path (default: alongside)")
    ren.add_argument("--target", choices=("dot", "flow", "maestro", "python"), default="dot",
                     help="emitter: DOT map (default), flow outline, a runnable Maestro flow, "
                          "or a runnable Python nav module (hooked replay + go_to_* helpers)")
    ren.set_defaults(func=_cmd_render)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (_UsageError, StaleRecordingError) as e:
        # StaleRecordingError = the recording predates the current identity version: a typed,
        # instant refusal naming the re-record requirement — a caller-artifact problem, not a run
        print(f"wendle: error: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
