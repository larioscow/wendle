"""Device-free contract test for scripts/demo_hook_frida.frida_probe — its 5 directive branches.

The on-device demo can only take ONE branch per run (a reachable Frida endpoint nearly always
yields cont). This pins ALL of them — cont + four honest stops — with no device, by faking the
two seams the probe reaches through: ctx.driver.shell (the `pidof` lookup) and subprocess.run
(the `frida` CLI). Closes the gap the adversarial review flagged: the probe shelled out via
subprocess and had ZERO test coverage, so "BRANCHES: cont or honest stop" was unverified.
"""
from __future__ import annotations

import subprocess
import types

import scripts.demo_hook_frida as demo
from wendle.replay.hooks import HookContext


class _Driver:
    """Minimal DeviceDriver stand-in: frida_probe only calls .shell (the pidof lookup)."""

    def __init__(self, pid_out: str):
        self._pid_out = pid_out

    def shell(self, cmd: str) -> str:
        return self._pid_out


def _ctx(pid_out: str, focus: str = demo.PKG) -> HookContext:
    return HookContext(driver=_Driver(pid_out), node_id="S0", namespace=f"{demo.PKG}/.Settings",
                       focus_pkg=focus, step_index=0, phase="before", params={}, graph=None, _data={})


def _fake_frida(monkeypatch, *, stdout: str = "", raises: Exception | None = None):
    def fake_run(cmd, **kw):
        if raises is not None:
            raise raises
        return types.SimpleNamespace(stdout=stdout, stderr="")
    monkeypatch.setattr(demo.subprocess, "run", fake_run)


def test_cont_when_frida_reads_a_live_process(monkeypatch):
    _fake_frida(monkeypatch, stdout="PROBE mods=377 arch=arm64\n")
    ctx = _ctx("5033")
    r = demo.frida_probe(ctx)
    assert r.kind == "cont"
    assert ctx._data["frida_pid"] == 5033
    assert ctx._data["frida_modules"] == 377
    assert ctx._data["frida_pid_is_foreground"] is True


def test_cross_check_false_when_engine_foreground_is_not_the_target(monkeypatch):
    # the PID came from `pidof PKG`, but the engine's verified foreground is something else
    # (e.g. a systemui overlay) -> the cross-check honestly records the mismatch.
    _fake_frida(monkeypatch, stdout="PROBE mods=377 arch=arm64\n")
    ctx = _ctx("5033", focus="com.android.systemui")
    r = demo.frida_probe(ctx)
    assert r.kind == "cont" and ctx._data["frida_pid_is_foreground"] is False


def test_stop_target_not_running_when_pidof_empty(monkeypatch):
    _fake_frida(monkeypatch, stdout="PROBE mods=377 arch=arm64\n")  # never consulted
    r = demo.frida_probe(_ctx(""))
    assert r.kind == "stop" and r.reason == "target_not_running"


def test_stop_attach_failed_when_no_probe_marker(monkeypatch):
    # frida connected but the agent never printed the marker (failed inject / wrong server version)
    _fake_frida(monkeypatch, stdout="unable to connect to remote frida-server: closed\n")
    r = demo.frida_probe(_ctx("5033"))
    assert r.kind == "stop" and r.reason == "frida_attach_failed"


def test_stop_unavailable_on_timeout(monkeypatch):
    _fake_frida(monkeypatch, raises=subprocess.TimeoutExpired(cmd="frida", timeout=30))
    r = demo.frida_probe(_ctx("5033"))
    assert r.kind == "stop" and r.reason == "frida_unavailable"


def test_stop_unavailable_when_frida_cli_missing(monkeypatch):
    _fake_frida(monkeypatch, raises=FileNotFoundError())
    r = demo.frida_probe(_ctx("5033"))
    assert r.kind == "stop" and r.reason == "frida_unavailable"


def test_stop_underinstrumented_when_too_few_modules(monkeypatch):
    _fake_frida(monkeypatch, stdout="PROBE mods=12 arch=arm64\n")
    r = demo.frida_probe(_ctx("5033"))
    assert r.kind == "stop" and r.reason == "frida_underinstrumented"
