#!/usr/bin/env python3
"""Export a project snapshot and start its local Superset dashboard."""

import argparse
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--port", type=int, default=18088)
args = parser.parse_args()
if not 1024 <= args.port <= 65535:
    parser.error("port must be between 1024 and 65535")
data = root / "data"
data.mkdir(exist_ok=True)
subprocess.run(
    [
        sys.executable,
        str(root / "export.py"),
        "--source",
        str(args.source.resolve()),
        "--output",
        str(data / "records.csv"),
    ],
    check=True,
)
env_file = root / ".env"
if not env_file.exists():
    values = {
        "BI_DB_PASSWORD": secrets.token_hex(24),
        "BI_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "SUPERSET_SECRET_KEY": secrets.token_hex(48),
    }
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
env = dict(
    os.environ, BI_PORT=str(args.port), BI_IMAGE="local/" + root.parent.name.lower() + "-bi:6.1.0"
)
compose = [
    "docker",
    "compose",
    "--env-file",
    str(env_file),
    "-p",
    root.parent.name.lower() + "-bi",
    "-f",
    str(root / "compose.yaml"),
]
subprocess.run([*compose, "build", "init"], check=True, env=env)
subprocess.run([*compose, "up", "-d", "--wait", "db"], check=True, env=env)
subprocess.run([*compose, "run", "--rm", "init"], check=True, env=env)
subprocess.run([*compose, "up", "-d", "superset"], check=True, env=env)
for _ in range(60):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=2) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Superset did not become ready; inspect docker compose logs superset")
print(
    f"Open http://localhost:{args.port}/superset/dashboard/project-bi/ "
    "(login: admin; password: bi/.env)"
)
