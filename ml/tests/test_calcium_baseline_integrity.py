"""Ordered, pinned baseline checks use only fictional data."""

import hashlib
import json

import numpy as np
import pytest
from spectralab_ml.evaluation.calcium_v4 import verified_baseline
from spectralab_ml.evaluation.campaign_shift_v2 import connected_groups
from spectralab_ml.evaluation.development_v3 import record_labels
from spectralab_ml.models.line_adapter_v3 import digest
from test_calcium_v4 import fixture as fixture


def test_verified_baseline_rejects_substitution_and_wrong_order(tmp_path, fixture):  # noqa: F811
    plan, _, _, _, profile = fixture
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "continuum-snr-ion").mkdir()
    records = [
        {
            "measurement_id": str(i),
            "physical_sample_id": f"p{i}",
            "family_id": f"f{i}",
            "session_id": f"s{i}",
            "input_sha256": f"hash{i}",
            "role": "development",
            "labels": {
                c: {"state": "negative" if i else "positive"} for c in ("Na", "Mg", "Ca", "Fe")
            },
            "composition_row": {"Na2O": 0.01, "MgO": 2.0, "CaO": 0.01 if i else 2.0, "FeOT": 2.0},
        }
        for i in range(4)
    ]
    saved = {"seed": 1, "records": records, "feature_plans": {"continuum-snr-ion": plan}}
    row = {"path": "run", "seed": 1}

    def pin():
        for key, relative in (
            ("protocol", "protocol.json"),
            ("result", "result.json"),
            ("profile", "profile.json"),
            ("oof", "continuum-snr-ion/development_oof.npz"),
        ):
            row[key + "_sha256"] = hashlib.sha256((folder / relative).read_bytes()).hexdigest()

    (folder / "protocol.json").write_text(json.dumps(saved))
    (folder / "profile.json").write_text(json.dumps(profile))
    result = {
        "profile": {"sha256": hashlib.sha256((folder / "profile.json").read_bytes()).hexdigest()}
    }
    (folder / "result.json").write_text(json.dumps(result))
    arrays = {
        "labels": record_labels(records),
        "detections": np.zeros((4, 4), bool),
        "measurement_ids": [r["measurement_id"] for r in records],
        "physical_ids": [r["physical_sample_id"] for r in records],
        "connected_groups": connected_groups(records),
    }
    target = folder / "continuum-snr-ion/development_oof.npz"
    np.savez(target, **arrays)
    pin()
    verified_baseline(tmp_path, row, records)
    arrays["detections"][0, 0] = True
    np.savez(target, **arrays)
    with pytest.raises(ValueError, match="integrity"):
        verified_baseline(tmp_path, row, records)
    pin()
    arrays["measurement_ids"] = list(reversed(arrays["measurement_ids"]))
    np.savez(target, **arrays)
    pin()
    with pytest.raises(ValueError, match="row identities"):
        verified_baseline(tmp_path, row, records)
    arrays["measurement_ids"] = list(reversed(arrays["measurement_ids"]))
    arrays["labels"] = arrays["labels"][::-1]
    np.savez(target, **arrays)
    pin()
    with pytest.raises(ValueError, match="row identities"):
        verified_baseline(tmp_path, row, records)
    arrays["labels"] = record_labels(records)
    np.savez(target, **arrays)
    profile.pop("identity")
    profile["plan"]["lines_nm"]["Na"] = [589.3]
    profile["identity"] = digest(profile)
    (folder / "profile.json").write_text(json.dumps(profile))
    pin()
    with pytest.raises(ValueError, match="original completed result"):
        verified_baseline(tmp_path, row, records)
    result["profile"]["sha256"] = row["profile_sha256"]
    (folder / "result.json").write_text(json.dumps(result))
    pin()
    with pytest.raises(ValueError, match="feature plan"):
        verified_baseline(tmp_path, row, records)
