from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from spectralab_ml.inference.measured import load_measured_profile
from spectralab_ml.models.line_adapter_v3 import BANDS, CLASSES

from app.core.config import Settings

logger = logging.getLogger("spectralab.instrument_profiles")


class InstrumentProfileRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._profiles: dict[str, dict[str, Any]] = {}
        self._loaded_models: dict[str, tuple[Any, dict[str, Any], str]] = {}
        self._discover_profiles()

    def _discover_profiles(self) -> None:
        trusted: dict[str, Path] = {}
        default_ca_v4 = (
            self.settings.project_root
            / "reports"
            / "adaptation_v4"
            / "calcium-zero-20261019T190232-12a3f9cb"
            / "research_profile.json"
        )
        if default_ca_v4.exists():
            trusted["calcium-v4"] = default_ca_v4

        if self.settings.instrument_profiles_path:
            extra_path = Path(self.settings.instrument_profiles_path).resolve()
            if extra_path.is_file():
                try:
                    data = json.loads(extra_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for pid, rel in data.items():
                            p = (extra_path.parent / rel).resolve()
                            trusted[pid] = p
                except Exception as exc:
                    logger.warning("Failed to parse instrument_profiles_path config: %s", exc)
            elif extra_path.is_dir():
                for p in extra_path.glob("*.json"):
                    if not p.is_symlink():
                        trusted[p.stem] = p

        for profile_id, path in trusted.items():
            self._register(profile_id, path)

    def register_profile(self, profile_id: str, path: Path) -> None:
        self._register(profile_id, path)

    def _register(self, profile_id: str, path: Path) -> None:
        try:
            model, metadata, sha256 = load_measured_profile(path)
            self._loaded_models[profile_id] = (model, metadata, sha256)
            class_order = metadata.get("class_order", CLASSES)
            profile_thresholds = metadata.get("thresholds", getattr(model, "thresholds", None))
            thresholds = (
                dict(zip(class_order, profile_thresholds, strict=True))
                if profile_thresholds is not None
                else {}
            )
            instrument_id = (
                metadata.get("provenance", {}).get("instrument_id")
                or metadata.get("instrument_id")
                or "chemcam-lanl-testbed"
            )
            self._profiles[profile_id] = {
                "profile_id": profile_id,
                "artifact_type": metadata.get("artifact_type", "unknown"),
                "schema_version": metadata.get("schema_version", 4),
                "identity": metadata.get("identity", ""),
                "sha256": sha256,
                "instrument_id": instrument_id,
                "supported_elements": list(metadata.get("class_order", CLASSES)),
                "unknown_elements": list(metadata.get("unknown_elements", ["H", "He"])),
                "thresholds": thresholds,
                "scale_requirements": {
                    "units": "nm",
                    "medium": "vacuum",
                    "conversion_supported": ["air"],
                    "conversion_method": "Peck-Reeder 1972 standard air",
                },
                "coverage_requirements": {
                    "bands_nm": BANDS,
                    "max_gap_nm": 0.5,
                    "max_sampling_step_nm": 0.5,
                    "max_points": 100_000,
                },
                "ready": True,
                "unavailable_reason": None,
                "qualification": metadata.get(
                    "qualification", "development_only_no_independent_blank_validation"
                ),
                "scope": metadata.get(
                    "scope", "adaptive_grouped_development_not_independent_final"
                ),
                "final_test_status": metadata.get("final_test_status", "not_performed"),
                "score_kind": model.score_kind,
            }
        except Exception as exc:
            logger.warning(
                "Failed to load instrument profile '%s' from %s: %s", profile_id, path, exc
            )
            self._profiles[profile_id] = {
                "profile_id": profile_id,
                "artifact_type": "unknown",
                "schema_version": 0,
                "identity": "",
                "sha256": "",
                "instrument_id": "unknown",
                "supported_elements": [],
                "unknown_elements": ["H", "He"],
                "thresholds": {},
                "scale_requirements": {
                    "units": "nm",
                    "medium": "vacuum",
                    "conversion_supported": ["air"],
                    "conversion_method": "Peck-Reeder 1972 standard air",
                },
                "coverage_requirements": {
                    "bands_nm": BANDS,
                    "max_gap_nm": 0.5,
                    "max_sampling_step_nm": 0.5,
                    "max_points": 100_000,
                },
                "ready": False,
                "unavailable_reason": f"Profile loading failed: {type(exc).__name__}",
                "qualification": "unverified",
                "scope": "unverified",
                "final_test_status": "not_performed",
                "score_kind": "unknown",
            }

    def list_profiles(self) -> list[dict[str, Any]]:
        return list(self._profiles.values())

    def get_profile_info(self, profile_id: str) -> dict[str, Any]:
        if profile_id not in self._profiles:
            raise FileNotFoundError(
                f"Instrument profile '{profile_id}' not found in trusted catalog"
            )
        return self._profiles[profile_id]

    def resolve_profile(self, profile_id: str) -> tuple[Any, dict[str, Any], str]:
        info = self.get_profile_info(profile_id)
        if not info["ready"]:
            raise ValueError(
                f"Instrument profile '{profile_id}' is unavailable: {info['unavailable_reason']}"
            )
        return self._loaded_models[profile_id]
