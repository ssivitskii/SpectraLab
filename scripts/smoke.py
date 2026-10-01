"""In-process real API → ML → storage smoke; no network/NIST calls."""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app
from fastapi.testclient import TestClient

with TestClient(app) as client:
    generated = client.post(
        "/api/v1/spectra/generate",
        json={"elements": ["H", "Na"], "component_weights": [1, 0.7], "seed": 42},
    )
    generated.raise_for_status()
    spectrum_id = generated.json()["spectrum_id"]
    assert client.get(f"/api/v1/spectra/{spectrum_id}").status_code == 200
    result = client.post(
        "/api/v1/predict",
        json={"input_type": "spectrum_id", "spectrum_id": spectrum_id, "model_id": "builtin-nnls"},
    )
    result.raise_for_status()
    payload = result.json()
    assert len(payload["scores"]) == len(client.get("/api/v1/elements").json()["elements"])
    assert payload["reference_source"] == "demo_fixture"
    path = Path("reports/smoke-response.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "spectrum_id": spectrum_id,
                "model_id": payload["model_id"],
                "detected_elements": payload["detected_elements"],
                "response": str(path),
            },
            ensure_ascii=False,
        )
    )
