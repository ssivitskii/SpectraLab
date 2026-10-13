import json

import numpy as np
import pytest
from spectralab_ml.models.line_adapter_v2 import (
    BANDS,
    CLASSES,
    LineAdapterV2,
    digest,
    extract_line_features,
)


def test_line_contrast_scale_invariance_and_background_rejection():
    grid = np.linspace(240, 900, 6601)
    plan = {
        "bands": BANDS,
        "class_order": CLASSES,
        "lines_nm": {c: [285.3] for c in CLASSES},
        "core_half_width_nm": 0.2,
        "sideband_inner_nm": 0.5,
        "sideband_outer_nm": 1.0,
    }
    background = np.full(len(grid), 2.0)
    emission = background + 10 * np.exp(-(((grid - 285.3) / 0.1) ** 2))
    features = extract_line_features(np.stack([background, emission, emission * 100]), grid, plan)
    for values in features.values():
        np.testing.assert_allclose(values[0], 0, atol=1e-8)
        np.testing.assert_allclose(values[1], values[2], rtol=1e-10)
        assert np.all(values[1] > 0)
    with pytest.raises(ValueError, match="No positive emission"):
        extract_line_features(np.zeros((1, len(grid))), grid, plan)


def fixture():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(40, 2))
    labels = np.tile((X[:, 0] > 0).astype(int)[:, None], (1, 4))
    labels[-4:] = -1
    return {c: X.copy() for c in CLASSES}, labels, [str(i) for i in range(40)]


def test_unknown_rows_do_not_fit_scalers_or_model_and_repeats_have_unit_weight():
    features, labels, groups = fixture()
    a = LineAdapterV2().fit(features, labels, groups, C=0.1)
    altered = {c: X.copy() for c, X in features.items()}
    for X in altered.values():
        X[-4:] = 1e6
    b = LineAdapterV2().fit(altered, labels, groups, C=0.1)
    np.testing.assert_allclose(a.scores(features), b.scores(features))
    duplicate = np.r_[np.arange(40), 0, 0]
    c = LineAdapterV2().fit(
        {k: v[duplicate] for k, v in features.items()},
        labels[duplicate],
        [groups[i] for i in duplicate],
        C=0.1,
    )
    np.testing.assert_allclose(a.scores(features), c.scores(features), atol=1e-7)


def test_safe_numeric_roundtrip_and_tamper_rejection(tmp_path):
    features, labels, groups = fixture()
    model = LineAdapterV2().fit(features, labels, groups, C=0.1)
    plan = {"class_order": CLASSES, "lines_nm": {c: [285.3] for c in CLASSES}}
    path = tmp_path / "profile.json"
    model.save(path, plan=plan, thresholds=[0.5] * 4, provenance={"scope": "development_only"})
    loaded, meta = LineAdapterV2.load(path)
    np.testing.assert_array_equal(model.scores(features), loaded.scores(features))
    assert meta["qualification"] == "development_only_no_independent_blank_validation"
    raw = json.loads(path.read_text())
    raw["states"]["Mg"]["scale"][0] = 0
    raw.pop("identity")
    raw["identity"] = digest(raw)
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="scaling"):
        LineAdapterV2.load(path)
