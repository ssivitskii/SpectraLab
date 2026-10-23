"""Run the local API, UI and worker; stop their process groups together."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port_from_environment(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer port") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


def launch_plan(
    api_port: int, ui_port: int, *, worker: bool = True
) -> list[tuple[str, list, dict]]:
    environment = os.environ.copy()
    frontend_environment = {**environment, "SPECTRALAB_API_TARGET": f"http://127.0.0.1:{api_port}"}
    commands = [
        (
            "API",
            [
                str(ROOT / ".venv/bin/uvicorn"),
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            environment,
        ),
        (
            "UI",
            [
                str(ROOT / "scripts/npm.sh"),
                "--prefix",
                "frontend",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(ui_port),
                "--strictPort",
            ],
            frontend_environment,
        ),
    ]
    if worker:
        commands.append(
            ("worker", [str(ROOT / ".venv/bin/python"), "-m", "app.worker"], environment)
        )
    return commands


def check_ports(api_port: int, ui_port: int) -> None:
    if api_port == ui_port:
        raise ValueError("API_PORT and UI_PORT must be different")
    for port in (api_port, ui_port):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ValueError(
                    f"Port {port} is unavailable. Stop the other server or set API_PORT/UI_PORT."
                ) from exc


def check_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for name, process in processes:
        if process.poll() is not None:
            raise RuntimeError(
                f"{name} stopped with exit code {process.returncode}; see its log above"
            )


def wait_until_ready(processes: list[tuple[str, subprocess.Popen]], urls: list[str]) -> None:
    deadline = time.monotonic() + 60
    pending = set(urls)
    while pending and time.monotonic() < deadline:
        check_processes(processes)
        for url in tuple(pending):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        pending.remove(url)
            except (OSError, urllib.error.URLError):
                pass
        if pending:
            time.sleep(0.2)
    if pending:
        raise RuntimeError(f"Startup timed out while waiting for {', '.join(sorted(pending))}")
    check_processes(processes)


def stop_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for _, process in processes:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
    for _, process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-worker", action="store_true", help="Use an already-running worker")
    args = parser.parse_args(argv)
    processes: list[tuple[str, subprocess.Popen]] = []

    def request_stop(_signal, _frame):
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, request_stop)
    try:
        api_port = port_from_environment("API_PORT", 8000)
        ui_port = port_from_environment("UI_PORT", 5173)
        if not (ROOT / ".venv/bin/uvicorn").is_file():
            raise ValueError("Python environment is missing; run make setup first")
        if not (ROOT / "frontend/node_modules/.bin/vite").is_file():
            raise ValueError("Frontend dependencies are missing; run make setup first")
        check_ports(api_port, ui_port)
        for name, command, environment in launch_plan(api_port, ui_port, worker=not args.no_worker):
            processes.append(
                (name, subprocess.Popen(command, cwd=ROOT, env=environment, start_new_session=True))
            )
        wait_until_ready(
            processes,
            [f"http://127.0.0.1:{api_port}/api/v1/health", f"http://127.0.0.1:{ui_port}"],
        )
        print(
            f"SpectraLab ready: http://localhost:{ui_port} · API: http://localhost:{api_port}/docs"
            + (" · worker enabled" if not args.no_worker else " · external worker"),
            flush=True,
        )
        while True:
            check_processes(processes)
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"SpectraLab startup failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
    finally:
        stop_processes(processes)
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
