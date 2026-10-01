"""Run both development servers; stop their complete process groups together."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
processes = []


def stop(_signal, _frame):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, stop)
try:
    commands = [
        [
            str(root / ".venv/bin/uvicorn"),
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            os.getenv("API_PORT", "8000"),
        ],
        [
            str(root / "scripts/npm.sh"),
            "--prefix",
            "frontend",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
        ],
    ]
    for command in commands:
        processes.append(subprocess.Popen(command, cwd=root, start_new_session=True))
    print("SpectraLab: http://localhost:5173 · API: http://localhost:8000/docs", flush=True)
    while all(p.poll() is None for p in processes):
        time.sleep(0.25)
    raise SystemExit(next((p.returncode for p in processes if p.returncode), 0))
except KeyboardInterrupt:
    pass
finally:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
