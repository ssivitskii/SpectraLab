import csv
import json
import subprocess
import sys
from pathlib import Path

EXPORT = Path(__file__).resolve().parents[2] / "bi" / "export.py"


def run(source, output):
    return subprocess.run(
        [sys.executable, str(EXPORT), "--source", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
    )


def job(job_id, status, config):
    return {
        "job_id": job_id,
        "status": status,
        "config_name": config,
        "created_at": "2026-11-04T10:00:00+00:00",
        "updated_at": "2026-11-04T10:02:00+00:00",
        "progress": {"completed": 1, "total": 2},
        "idempotency_key": "private",
        "seeds": [42],
    }


def test_job_storage_export(tmp_path):
    source = tmp_path / "jobs"
    source.mkdir()
    for name, status, config in [("a", "completed", "demo"), ("b", "running", "default")]:
        (source / f"{name}.json").write_text(json.dumps(job(name, status, config)))
    output = tmp_path / "out.csv"
    result = run(source, output)
    assert result.returncode == 0, result.stderr
    with output.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0] == {
        "created_at": "2026-11-04T10:00:00+00:00",
        "status": "completed",
        "config_name": "demo",
        "evidence": "unknown",
        "elapsed_seconds": "120.0",
        "completed": "1",
        "total": "2",
    }
    assert rows[1]["elapsed_seconds"] == "" and rows[1]["evidence"] == "unknown"
    assert "private" not in output.read_text()


def test_missing_and_damaged_source_fail(tmp_path):
    output = tmp_path / "out.csv"
    assert run(tmp_path / "missing", output).returncode != 0
    source = tmp_path / "jobs"
    source.mkdir()
    (source / "bad.json").write_text("{")
    assert run(source, output).returncode != 0
    (source / "bad.json").write_text(json.dumps(job("other", "completed", "demo")))
    assert run(source, output).returncode != 0
    assert not output.exists()


def test_empty_store_has_header(tmp_path):
    output = tmp_path / "out.csv"
    assert run(tmp_path, output).returncode == 0
    assert (
        output.read_text().strip()
        == "created_at,status,config_name,evidence,elapsed_seconds,completed,total"
    )
