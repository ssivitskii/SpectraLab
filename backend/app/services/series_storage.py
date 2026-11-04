"""Durable storage for shot series analysis results, receipts, and exported spectra."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.services.storage import SAFE_SPECTRUM_ID, directory_lock

ALLOWED_SERIES_FILES = {
    "receipt.json",
    "aggregate.csv",
    "dispersion.csv",
    "metadata.json",
    "raw_input.csv",
}


class SeriesStorage:
    def __init__(
        self,
        directory: Path,
        max_stored: int = 500,
        max_storage_bytes: int = 500_000_000,
    ):
        self.directory = directory.resolve()
        self.max_stored = max_stored
        self.max_storage_bytes = max_storage_bytes
        self.directory.mkdir(parents=True, exist_ok=True)

    def _validate_id(self, series_id: str) -> None:
        if not SAFE_SPECTRUM_ID.fullmatch(series_id):
            raise ValueError(f"Invalid series_id: '{series_id}'")

    def _series_dir(self, series_id: str) -> Path:
        self._validate_id(series_id)
        return self.directory / series_id

    def save(
        self,
        series_id: str,
        receipt: dict[str, Any],
        files: dict[str, bytes],
        raw_csv: bytes | None = None,
    ) -> dict[str, Any]:
        self._validate_id(series_id)
        final_dir = self.directory / series_id
        if final_dir.exists():
            raise ValueError(f"Series '{series_id}' already exists")

        tmp_dir = self.directory / f".{series_id}.{uuid.uuid4().hex}.tmp"
        tmp_dir.mkdir(parents=True, exist_ok=False)

        try:
            # 1. Write receipt.json
            receipt_bytes = (
                json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            ).encode("utf-8")
            (tmp_dir / "receipt.json").write_bytes(receipt_bytes)

            # 2. Write auxiliary files (aggregate.csv, dispersion.csv, metadata.json)
            for name, content in files.items():
                if name in ALLOWED_SERIES_FILES:
                    (tmp_dir / name).write_bytes(content)

            # 3. Write raw_input.csv if provided
            if raw_csv is not None:
                (tmp_dir / "raw_input.csv").write_bytes(raw_csv)

            with directory_lock(self.directory):
                os.replace(tmp_dir, final_dir)
                self._prune_locked()
        except BaseException:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        return receipt

    def get(self, series_id: str) -> dict[str, Any]:
        series_dir = self._series_dir(series_id)
        receipt_path = series_dir / "receipt.json"
        if not series_dir.is_dir() or not receipt_path.is_file() or receipt_path.is_symlink():
            raise FileNotFoundError(f"Series '{series_id}' was not found")
        try:
            return json.loads(receipt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f"Stored receipt for series '{series_id}' is damaged") from exc

    def get_file(self, series_id: str, filename: str) -> bytes:
        if filename not in ALLOWED_SERIES_FILES:
            raise ValueError(f"File '{filename}' is not an accessible series export")
        series_dir = self._series_dir(series_id)
        file_path = series_dir / filename
        if not file_path.is_file() or file_path.is_symlink():
            raise FileNotFoundError(f"File '{filename}' not found for series '{series_id}'")
        return file_path.read_bytes()

    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        items: list[dict[str, Any]] = []
        for entry in self.directory.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            receipt_path = entry / "receipt.json"
            if not receipt_path.is_file():
                continue
            try:
                data = json.loads(receipt_path.read_text(encoding="utf-8"))
                meas = data.get("measurement", {})
                items.append(
                    {
                        "series_id": data.get("series_id", entry.name),
                        "created_at": data.get("created_at", ""),
                        "status": data.get("status", "unknown"),
                        "shot_count": data.get("shot_count", 0),
                        "accepted_shot_count": data.get("accepted_shot_count", 0),
                        "measurement_id": meas.get("measurement_id"),
                        "physical_sample_id": meas.get("physical_sample_id"),
                        "family_id": meas.get("family_id"),
                        "session_id": meas.get("session_id"),
                        "instrument_id": meas.get("instrument_id"),
                    }
                )
            except (OSError, ValueError):
                continue

        items.sort(key=lambda x: (x["created_at"], x["series_id"]), reverse=True)
        total = len(items)
        return items[offset : offset + limit], total

    def delete(self, series_id: str) -> bool:
        series_dir = self._series_dir(series_id)
        with directory_lock(self.directory):
            if not series_dir.is_dir():
                return False
            shutil.rmtree(series_dir, ignore_errors=True)
            return True

    def _prune_locked(self) -> None:
        entries: list[tuple[float, int, Path]] = []
        total_bytes = 0
        for entry in self.directory.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            dir_size = 0
            for f in entry.rglob("*"):
                if f.is_file() and not f.is_symlink():
                    dir_size += f.stat().st_size
            total_bytes += dir_size
            mtime = entry.stat().st_mtime
            entries.append((mtime, dir_size, entry))

        # Sort oldest first
        entries.sort(key=lambda t: t[0])
        while entries and (len(entries) > self.max_stored or total_bytes > self.max_storage_bytes):
            _, dir_size, oldest = entries.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            total_bytes -= dir_size
