from __future__ import annotations

import errno
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

SAFE_SPECTRUM_ID = re.compile(r"^[a-f0-9]{32}$")


class SpectrumStorage:
    def __init__(self, directory: Path, max_files: int = 500, max_bytes: int = 500_000_000):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        spectrum_id = uuid.uuid4().hex
        record = {"spectrum_id": spectrum_id, **payload}
        destination = self.directory / f"{spectrum_id}.json"
        temporary = destination.with_suffix(".tmp")
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        with self._lock:
            files = list(self.directory.glob("*.json"))
            if (
                len(files) >= self.max_files
                or sum(p.stat().st_size for p in files) + len(encoded) > self.max_bytes
            ):
                raise OSError(errno.ENOSPC, "Configured spectrum storage quota exceeded")
            temporary.write_bytes(encoded)
            temporary.replace(destination)
        return record

    def get(self, spectrum_id: str) -> dict[str, Any]:
        if not SAFE_SPECTRUM_ID.fullmatch(spectrum_id):
            raise ValueError("Invalid spectrum_id")
        path = self.directory / f"{spectrum_id}.json"
        if path.is_symlink() or path.resolve().parent != self.directory or not path.is_file():
            raise FileNotFoundError(f"Spectrum '{spectrum_id}' was not found")
        return json.loads(path.read_text(encoding="utf-8"))


class ExperimentStorage:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        records = []
        for path in sorted(self.directory.glob("*/result.json"), reverse=True):
            try:
                if path.is_symlink() or path.parent.is_symlink():
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                records.append(
                    {
                        "run_id": payload["run_id"],
                        "status": payload["status"],
                        "created_at": payload["created_at"],
                        "reference_source": payload["reference_source"],
                        "protocol": payload["protocol"],
                        "demo": payload.get("demo", False),
                    }
                )
            except (KeyError, json.JSONDecodeError):
                continue
        return records

    def get(self, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", run_id):
            raise ValueError("Invalid experiment id")
        path = self.directory / run_id / "result.json"
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or path.resolve().parent.parent != self.directory
            or not path.is_file()
        ):
            raise FileNotFoundError(f"Experiment '{run_id}' was not found")
        return json.loads(path.read_text(encoding="utf-8"))
