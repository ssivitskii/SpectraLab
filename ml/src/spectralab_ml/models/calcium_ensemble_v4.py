"""Safe four-member calcium ensemble from adaptive grouped development."""

from __future__ import annotations

import copy
import json
import os
import stat
from pathlib import Path

import numpy as np

from spectralab_ml.models.calcium_adapter_v4 import (
    CalciumAdapterV4,
    CalciumClassifier,
    calcium_features,
    feature_dimension,
)
from spectralab_ml.models.line_adapter_v3 import _object, digest


class CalciumEnsembleV4(CalciumAdapterV4):
    """Mean member probabilities, unconditional Ca; frozen v3 for Na/Mg/Fe."""

    score_kind = "adaptive_calcium_probability_platt_development"

    def predict_spectra(self, X, grid):
        result = self.original.predict_spectra(X, grid)
        features = calcium_features(X, grid, self.original.plan)[self.family]
        p = np.mean([m.probabilities(features) for m in self.members], axis=0)
        result["probabilities"][:, 2] = p
        result["raw_scores"][:, 2] = np.mean([m.raw_scores(features) for m in self.members], axis=0)
        result["detections"][:, 2] = p >= self.thresholds[2]
        result["accepted"][:, 2] = True
        result["decisions"][:, 2] = result["detections"][:, 2].astype(int)
        return result

    @classmethod
    def from_payload(cls, raw):
        if (
            not isinstance(raw, dict)
            or raw.get("artifact_type") != "calcium_ensemble_v4"
            or raw.get("aggregation") != "mean_of_four_calibrated_probabilities"
            or not isinstance(raw.get("members"), list)
            or len(raw["members"]) != 4
            or "calcium" in raw
        ):
            raise ValueError("Unsupported four-member calcium ensemble")
        single = copy.deepcopy(raw)
        single["artifact_type"] = "calcium_adapter_v4"
        single["calcium"] = single.pop("members")[0]
        base = CalciumAdapterV4.from_payload(single)
        model = cls()
        model.original, model.plan = base.original, base.plan
        model.family, model.thresholds = base.family, base.thresholds
        dimension = feature_dimension(model.plan, model.family)
        model.members = [CalciumClassifier.from_payload(s, dimension) for s in raw["members"]]
        return model

    @classmethod
    def save(cls, path, *, original_profile, members, family, threshold, provenance):
        from spectralab_ml.models.calcium_adapter_v4 import SCOPE
        from spectralab_ml.models.line_adapter_v3 import CLASSES, QUALIFICATION

        raw = {
            "schema_version": 4,
            "artifact_type": "calcium_ensemble_v4",
            "aggregation": "mean_of_four_calibrated_probabilities",
            "scope": SCOPE,
            "qualification": QUALIFICATION,
            "final_test_status": "not_performed",
            "calcium_feature_family": family,
            "calcium_selective_refusal": False,
            "class_order": CLASSES,
            "calibrated": True,
            "score_kind": cls.score_kind,
            "unknown_elements": ["H", "He"],
            "original_v3_profile": original_profile,
            "members": members,
            "calcium_threshold": float(threshold),
            "provenance": {
                **provenance,
                "instrument_id": original_profile["provenance"]["instrument_id"],
                "decision_policy": original_profile["provenance"]["decision_policy"],
            },
        }
        cls.from_payload(raw)
        raw["identity"] = digest(raw)
        buffer = (json.dumps(raw, indent=2, allow_nan=False) + "\n").encode()
        if len(buffer) > 5_000_000:
            raise ValueError("Calcium ensemble exceeds size limit")
        with Path(path).open("xb") as stream:
            stream.write(buffer)

    @classmethod
    def load(cls, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 5_000_000:
                raise ValueError("Invalid calcium ensemble artifact file")
            buffer = stream.read(5_000_001)
        if len(buffer) > 5_000_000:
            raise ValueError("Calcium ensemble exceeds size limit")
        raw = json.loads(buffer, object_pairs_hook=_object)
        if not isinstance(raw, dict):
            raise ValueError("Calcium ensemble must be an object")
        identity = raw.pop("identity", None)
        if digest(raw) != identity:
            raise ValueError("Calcium ensemble integrity mismatch")
        return cls.from_payload(raw), {**raw, "identity": identity}
