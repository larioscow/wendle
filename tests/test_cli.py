"""The stable v1 CLI (`wendle`) — a THIN, honesty-preserving shell over the library verbs.

Exit codes ARE the honesty contract, made shell-visible:
    0 = verified success       (replay completed / navigate arrived)
    2 = usage error            (bad flags, missing file, unknown node, bad hooks file)
    3 = HONEST STOP / refusal  (stopped / arrived_unverified / off_graph / no_route / ...)
Crashes propagate (Python's own exit 1) — never masked as success or as a refusal.

The e2e tests run the REAL engine/navigator over a scripted FakeDriver (no engine
internals patched); only the driver factory seam `cli._make_driver` is replaced.
The CLI must never print a credential: `--param` values stay out of stdout/stderr.
"""
import textwrap

import pytest

from wendle import __version__, cli
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.compose import resolve_profile
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import NavOutcome
from wendle.replay.engine import replay_recording
from wendle.replay.hooks import stop
from wendle.replay.result import ReplayResult

NS = "com.app/.AActivity"
XML = (
    '<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
    'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
    '<node class="android.widget.EditText" package="com.app" resource-id="com.app:id/pwd" '
    'clickable="true" content-desc="" text="" bounds="[40,500][1040,620]"/>'
    "</node></hierarchy>"
)
FID = fingerprint(NS, XML, resolve_profile(XML, NS))  # the REAL id of XML — EXACT tier matches


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (
        f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
        f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}",
    )


def _graph():
    """One verified-anchor screen with a single same-screen sensitive set_text edge.

    Same-screen on purpose: the scripted device never changes, so the run is robust
    to HOW MANY times the engine observes (no brittle dump-count choreography).
    """
    g = Graph()
    g.upsert_screen(
        Screen(id=FID, namespace=NS, package="com.app", activity=".AActivity",
               structure_id=structure_id(NS, XML),
               force_action=ForceAction("am_start", NS, verified_fp=FID))
    )
    g.add_transition(
        Transition(source=FID, target=FID, action=Action(
            selector=Selector("resource_id", "com.app:id/pwd"), action_type="set_text",
            value={"param": "password"}, sensitive=True))
    )
    return g


def _drv():
    return FakeDriver(hierarchies=[XML], dumpsys_pairs=[_dumpsys(NS)],
                      present_selectors={("resource_id", "com.app:id/pwd")})


@pytest.fixture()
def rec(tmp_path):
    p = tmp_path / "rec.json"
    _graph().save(str(p))
    return str(p)


# ---- pure shell: version / help / listing / render (no device, no engine) ----

def test_version_prints_library_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_lists_every_subcommand(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for sub in ("record", "replay", "navigate", "nodes", "render"):
        assert sub in out


def test_nodes_lists_ids_and_marks_anchors(rec, capsys):
    assert cli.main(["nodes", rec]) == 0
    out = capsys.readouterr().out
    assert FID in out and NS in out
    assert "anchor" in out  # the verified entry point is called out


def test_render_writes_redaction_safe_dot(rec, tmp_path, capsys):
    out_path = str(tmp_path / "map.dot")
    assert cli.main(["render", rec, "-o", out_path]) == 0
    blob = open(out_path).read()
    assert "digraph" in blob
    assert out_path in capsys.readouterr().out


# ---- exit-code mapping units (the honesty contract table itself) ----

def test_replay_exit_codes():
    assert cli._exit_for_replay(ReplayResult("completed")) == 0
    assert cli._exit_for_replay(ReplayResult("stopped")) == 3


@pytest.mark.parametrize("status,code", [
    ("arrived", 0),
    ("arrived_unverified", 3),  # plausible-but-unconfirmed is NOT shell success
    ("off_graph", 3),
    ("content_drift", 3),
    ("cross_app_boundary", 3),
    ("force_failed", 3),
    ("no_route", 3),
])
def test_navigate_exit_codes(status, code):
    assert cli._exit_for_nav(NavOutcome(status)) == code


def test_param_parsing_requires_key_value(rec, capsys):
    assert cli.main(["replay", rec, "--param", "no_equals_sign"]) == 2
    assert "k=v" in capsys.readouterr().err


def test_missing_recording_is_a_usage_error(capsys):
    assert cli.main(["replay", "/nope/missing.json"]) == 2
    assert "missing.json" in capsys.readouterr().err


# ---- replay e2e: REAL engine over a scripted device ----

def test_replay_completes_param_injected_and_never_printed(rec, monkeypatch, capsys):
    drv = _drv()
    monkeypatch.setattr(cli, "_make_driver", lambda serial: drv)
    rc = cli.main(["replay", rec, "--param", "password=hunter2"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "completed" in captured.out
    # the credential reached the device...
    assert any("hunter2" in str(t) for t in drv.text_sets)
    # ...and NEVER the terminal (redaction-by-default)
    assert "hunter2" not in captured.out + captured.err


def test_replay_hook_stop_is_honest_exit_3(rec, tmp_path, monkeypatch, capsys):
    hooks_py = tmp_path / "my_hooks.py"
    hooks_py.write_text(textwrap.dedent(
        """
        from wendle.replay.hooks import HookRegistry, stop

        hooks = HookRegistry()

        @hooks.before(0)
        def gate(ctx):
            return stop("paywall_undecided")
        """
    ))
    drv = _drv()
    monkeypatch.setattr(cli, "_make_driver", lambda serial: drv)
    rc = cli.main(["replay", rec, "--hooks", str(hooks_py)])
    captured = capsys.readouterr()
    assert rc == 3
    assert "paywall_undecided" in captured.out + captured.err
    assert drv.text_sets == []  # the gated step never ran


def test_hooks_file_without_registry_is_usage_error(rec, tmp_path, capsys):
    bad = tmp_path / "empty_hooks.py"
    bad.write_text("x = 1\n")
    assert cli.main(["replay", rec, "--hooks", str(bad)]) == 2
    assert "HookRegistry" in capsys.readouterr().err


# ---- navigate e2e: REAL navigator; --from defaults to the sole verified anchor ----

def test_navigate_arrives_exit_0(rec, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_make_driver", lambda serial: _drv())
    rc = cli.main(["navigate", rec, "--to", FID])  # --from omitted: sole anchor
    assert rc == 0
    assert "arrived" in capsys.readouterr().out


def test_navigate_unknown_target_is_usage_error(rec, capsys):
    assert cli.main(["navigate", rec, "--to", "S404"]) == 2
    err = capsys.readouterr().err
    assert "S404" in err and "nodes" in err  # points at `wendle nodes` to discover ids


# ---- record: flag→verb wiring (the verb itself is covered by test_record_loop) ----

def test_record_flags_reach_the_verb(tmp_path, monkeypatch, capsys):
    got = {}

    def fake_record(driver=None, **kw):
        got.update(kw, driver=driver)
        return _graph()

    monkeypatch.setattr(cli, "record", fake_record)
    monkeypatch.setattr(cli, "_make_driver", lambda serial: FakeDriver())
    out_path = str(tmp_path / "r.json")
    rc = cli.main(["record", "--out", out_path, "--duration", "30"])
    assert rc == 0
    assert got["duration"] == 30.0 and got["out"] == out_path
    assert isinstance(got["driver"], FakeDriver)
    summary = capsys.readouterr().out
    assert "screen" in summary and "transition" in summary


# ---- library: replay_recording must forward hooks/on_step to the run ----

def test_replay_recording_forwards_hooks_and_on_step():
    seen = []
    res = replay_recording(
        _graph(), _drv(),
        hooks={"before:0": [lambda ctx: stop("gate")]},
        on_step=seen.append,
        clock=(lambda t=[0.0]: t[0]),  # injected: no real waiting
        sleep=lambda dt: None,
        settle_kwargs={"sleep": lambda dt: None},
    )
    assert res.status == "stopped"
    assert "gate" in (res.failed_step.error or "")
    assert seen  # on_step saw the hook's honest stop step
