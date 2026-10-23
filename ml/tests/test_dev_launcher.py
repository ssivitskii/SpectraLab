"""Development launcher regressions: addressing, fail-fast and child cleanup."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "dev_launcher", Path(__file__).resolve().parents[2] / "scripts/dev.py"
)
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


def test_custom_ports_reach_api_proxy_and_vite_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("SPECTRALAB_API_TARGET", "http://127.0.0.1:9999")
    plan = {
        name: (command, environment) for name, command, environment in dev.launch_plan(8100, 5200)
    }
    assert plan["API"][0][-2:] == ["--port", "8100"]
    assert plan["UI"][1]["SPECTRALAB_API_TARGET"] == "http://127.0.0.1:8100"
    assert plan["UI"][0][-3:] == ["--port", "5200", "--strictPort"]
    assert "worker" in plan
    assert "worker" not in {name for name, *_ in dev.launch_plan(8100, 5200, worker=False)}


@pytest.mark.parametrize("value", ["bad", "0", "-1", "65536"])
def test_invalid_ports_have_actionable_errors(value, monkeypatch):
    monkeypatch.setenv("API_PORT", value)
    with pytest.raises(ValueError, match="API_PORT"):
        dev.port_from_environment("API_PORT", 8000)


def test_identical_api_and_ui_ports_fail_before_binding():
    with pytest.raises(ValueError, match="must be different"):
        dev.check_ports(8000, 8000)


def test_clean_early_child_exit_is_a_startup_failure():
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="UI stopped with exit code 0"):
        dev.wait_until_ready([("UI", process)], ["http://127.0.0.1:5200"])


def test_missing_installation_fails_before_launching_any_child(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dev, "ROOT", tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("No child should launch until dependencies have been checked")

    monkeypatch.setattr(dev.subprocess, "Popen", forbidden)
    with pytest.raises(SystemExit) as exc:
        dev.main([])
    assert exc.value.code == 1
    assert "make setup" in capsys.readouterr().err


def test_stop_reaps_both_live_and_already_exited_processes():
    exited = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    exited.wait(timeout=5)
    live = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        dev.stop_processes([("finished", exited), ("live", live)])
        assert live.poll() is not None
        assert exited.returncode == 0
    finally:
        if live.poll() is None:
            live.kill()
            live.wait(timeout=5)
