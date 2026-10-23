"""Serve an isolated real backend for browser tests; no user's artifacts are touched."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import uvicorn
from app.core.config import Settings
from spectralab_ml.config import load_project_config, repository_root
from spectralab_ml.data.reference import load_demo_catalog
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import train_and_save_models


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="spectralab-e2e-") as temporary:
        root = Path(temporary)
        project = load_project_config()
        catalog = load_demo_catalog()
        processor = SpectrumPreprocessor(
            project.wavelength_start_nm, project.wavelength_end_nm, project.model_step_nm
        )
        dataset = build_demo_dataset(
            catalog, processor, project.elements, seed=42, base_samples=18, variations=1
        )
        train_and_save_models(
            dataset,
            catalog,
            processor,
            root / "artifacts/models",
            seed=42,
            model_ids=["baseline-nnls", "line-logistic", "line-logistic-robust"],
        )
        experiment = root / "reports/experiments/e2e-fixture"
        experiment.mkdir(parents=True)
        (experiment / "result.json").write_text(
            json.dumps(
                {
                    "run_id": "e2e-fixture",
                    "status": "completed",
                    "created_at": "2026-01-01T00:00:00Z",
                    "reference_source": "demo_fixture",
                    "protocol": "iid_grouped",
                    "demo": True,
                    "configuration": {"schema_version": 1},
                    "seed": 42,
                    "runs": [],
                    "limitations": "E2E catalog fixture; no experiment quality claim",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            project_root=repository_root(),
            data_dir=root / "data",
            artifacts_dir=root / "artifacts",
            reports_dir=root / "reports",
            cors_origins=["http://127.0.0.1:5174"],
        )
        from app import main as api

        api.settings = settings
        from app.worker import worker_environment

        worker = subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--batches-only"],
            env=worker_environment(settings),
        )
        try:
            uvicorn.run(api.app, host="127.0.0.1", port=8001)
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)


if __name__ == "__main__":
    main()
