from __future__ import annotations

import json
from pathlib import Path

from app.main import app

destination = Path(__file__).resolve().parents[1] / "artifacts" / "openapi.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print(destination)
