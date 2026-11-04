#!/usr/bin/env python3
"""Export SpectraLab JobStorage records without request or user identifiers."""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

COLUMNS = (
    "created_at",
    "status",
    "config_name",
    "evidence",
    "elapsed_seconds",
    "completed",
    "total",
)
STATUSES = {"queued", "running", "completed", "failed", "cancelled"}


def convert(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("job_id") != path.stem:
        raise ValueError(f"{path}: invalid job identity")
    status, config = data["status"], data["config_name"]
    progress = data["progress"]
    if (
        status not in STATUSES
        or config not in {"demo", "default"}
        or not isinstance(progress, dict)
    ):
        raise ValueError(f"{path}: invalid job fields")
    completed, total = progress["completed"], progress["total"]
    if any(type(x) is not int or x < 0 for x in (completed, total)) or completed > total:
        raise ValueError(f"{path}: invalid progress")
    created = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    updated = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    if created.tzinfo is None or updated.tzinfo is None or updated < created:
        raise ValueError(f"{path}: invalid timestamps")
    elapsed = (
        (updated - created).total_seconds()
        if status in {"completed", "failed", "cancelled"}
        else ""
    )
    return (data["created_at"], status, config, "unknown", elapsed, completed, total)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="JobStorage directory, normally artifacts/jobs"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.source.is_dir():
            raise ValueError(f"JobStorage directory does not exist: {args.source}")
        paths = sorted(p for p in args.source.glob("*.json") if not p.name.startswith("."))
        if any(p.is_symlink() for p in paths):
            raise ValueError("JobStorage contains a symlink")
        rows = [convert(p) for p in paths]
        with args.output.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"export failed: {exc}\n")


if __name__ == "__main__":
    main()
