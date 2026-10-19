from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from spectralab_ml.models.line_adapter_v2 import extract_line_features
from spectralab_ml.models.line_adapter_v3 import (
    BANDS,
    CLASSES,
    DECISION_POLICY,
    GRID,
    LineAdapterV3,
    digest,
    extract_features,
    validate_labels,
)


@pytest.fixture
def spectral_fixture():
    centers = [589.2, 285.3, 422.8, 404.7]
    plan = {
        "schema_version": 3,
        "variant": "continuum-snr-ion",
        "class_order": CLASSES,
        "wavelength_medium": "vacuum",
        "bands": BANDS,
        "grid_nm": {"start": 240.0, "end": 900.0, "points": 6601},
        "lines_nm": {c: [v, v + 3] for c, v in zip(CLASSES, centers, strict=True)},
        "stages": {c: [1, 2] for c in CLASSES},
        "core_half_width_nm": 0.2,
        "sideband_inner_nm": 0.5,
        "sideband_outer_nm": 1.0,
        "robust_noise_floor_fraction": 0.001,
        "snr_cap": 1000.0,
    }
    labels = np.array([[int(bool(i & (1 << k))) for k in range(4)] for i in range(16)])
    rng = np.random.default_rng(42)
    spectra = 2 + (GRID - 240)[None, :] * 0.002 + rng.normal(0, 0.04, (16, len(GRID)))
    for k, center in enumerate(centers):
        spectra += (0.01 + labels[:, k, None] * 5) * np.exp(-(((GRID - center) / 0.08) ** 2))
        spectra += (0.01 + labels[:, k, None] * 2) * np.exp(-(((GRID - center - 3) / 0.08) ** 2))
    return plan, spectra, labels


def fitted_model(fixture):
    plan, spectra, labels = fixture
    features, _ = extract_features(spectra, GRID, plan)
    model = LineAdapterV3().fit(features, labels, [f"fit-{i}" for i in range(16)], C=0.1)
    model.fit_calibration(features, labels, [f"cal-{i}" for i in range(16)])
    model.plan, model.thresholds = plan, [0.5] * 4
    model.selective = {c: {"minimum_snr": 2, "margin": 0.1} for c in CLASSES}
    return model


def test_linear_continuum_removed_and_gain_invariant(spectral_fixture):
    plan, spectra, _ = spectral_fixture
    features, snr = extract_features(spectra, GRID, plan)
    changed, changed_snr = extract_features(spectra * 137, GRID, plan)
    np.testing.assert_allclose(snr, changed_snr, rtol=1e-11, atol=1e-11)
    for c in CLASSES:
        assert features[c].shape == (16, 9)
        np.testing.assert_allclose(features[c], changed[c], rtol=1e-11, atol=1e-11)
    flat_ramp = (2 + 0.002 * (GRID - 240))[None, :]
    corrected, snr = extract_features(flat_ramp, GRID, plan)
    assert snr.max() < 1e-9
    assert max(v.max() for v in corrected.values()) < 1e-9


def test_baseline_features_remain_exactly_v2(spectral_fixture):
    plan, spectra, _ = spectral_fixture
    plan["variant"] = "v2-line-baseline"
    actual, _ = extract_features(spectra, GRID, plan)
    expected = extract_line_features(spectra, GRID, plan)
    for c in CLASSES:
        np.testing.assert_array_equal(actual[c], expected[c])


def test_unknown_rows_and_replicate_count_do_not_change_fit_or_calibration(spectral_fixture):
    plan, spectra, labels = spectral_fixture
    features, _ = extract_features(spectra, GRID, plan)
    base = fitted_model(spectral_fixture)
    # All-unknown measurements must never affect fitted scaling, regression or Platt.
    extended = {c: np.vstack([v, np.full((1, v.shape[1]), 1e9)]) for c, v in features.items()}
    extended_labels = np.vstack([labels, [-1] * 4])
    other = LineAdapterV3().fit(extended, extended_labels, [f"fit-{i}" for i in range(17)], C=0.1)
    other.fit_calibration(extended, extended_labels, [f"cal-{i}" for i in range(17)])
    np.testing.assert_allclose(
        base.probabilities(features), other.probabilities(features), atol=1e-12
    )
    # Replicating one physical sample cannot increase its training/calibration weight.
    duplicated = {c: np.vstack([v, v[:1], v[:1]]) for c, v in features.items()}
    duplicated_labels = np.vstack([labels, labels[:1], labels[:1]])
    repeated = LineAdapterV3().fit(
        duplicated, duplicated_labels, [f"fit-{i}" for i in range(16)] + ["fit-0"] * 2, C=0.1
    )
    repeated.fit_calibration(
        duplicated, duplicated_labels, [f"cal-{i}" for i in range(16)] + ["cal-0"] * 2
    )
    np.testing.assert_allclose(
        base.probabilities(features), repeated.probabilities(features), atol=1e-7
    )


def test_calibration_refuses_fit_sample_and_conflicting_replicate(spectral_fixture):
    plan, spectra, labels = spectral_fixture
    features, _ = extract_features(spectra, GRID, plan)
    model = LineAdapterV3().fit(features, labels, [str(i) for i in range(16)], C=0.1)
    with pytest.raises(ValueError, match="disjoint"):
        model.fit_calibration(features, labels, [str(i) for i in range(16)])
    with pytest.raises(ValueError, match="Conflicting"):
        validate_labels(labels, ["duplicate"] * 16)


def save_model(path, fixture):
    model = fitted_model(fixture)
    model.save(
        path,
        plan=model.plan,
        thresholds=model.thresholds,
        selective=model.selective,
        provenance={
            "instrument_id": "synthetic-testbed",
            "decision_policy": copy.deepcopy(DECISION_POLICY),
            "scope": "nested_grouped_development_cv_not_independent_test",
        },
    )
    return model


def test_safe_numeric_roundtrip_preserves_all_predictions(tmp_path, spectral_fixture):
    path = tmp_path / "profile.json"
    model = save_model(path, spectral_fixture)
    loaded, metadata = LineAdapterV3.load(path)
    assert metadata["final_test_status"] == "not_performed"
    assert metadata["unknown_elements"] == ["H", "He"]
    expected = model.predict_spectra(spectral_fixture[1], GRID)
    actual = loaded.predict_spectra(spectral_fixture[1], GRID)
    for key in expected:
        np.testing.assert_array_equal(actual[key], expected[key])
    loaded.fit(
        extract_features(spectral_fixture[1], GRID, model.plan)[0],
        spectral_fixture[2],
        [str(i) for i in range(16)],
        C=0.1,
    )
    with pytest.raises(ValueError, match="not been fitted"):
        loaded.predict_spectra(spectral_fixture[1], GRID)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["states"]["Fe"]["scale"].__setitem__(0, 0),
        lambda p: p["states"]["Fe"].__setitem__("coef", [1.0]),
        lambda p: p["calibration"]["Fe"].__setitem__("slope", -1),
        lambda p: p["plan"].__setitem__("wavelength_medium", "air"),
        lambda p: p["plan"]["lines_nm"].__setitem__("Fe", [469.0]),
        lambda p: p["plan"].__setitem__("sideband_outer_nm", 2),
        lambda p: p["thresholds"].__setitem__(0, True),
        lambda p: p["selective"]["Fe"].__setitem__("margin", 0.3),
        lambda p: p.__setitem__("final_test_status", "passed"),
        lambda p: p.__setitem__("unknown_elements", []),
        lambda p: p["provenance"].__setitem__("instrument_id", 123),
        lambda p: p["provenance"]["decision_policy"]["Fe"].__setitem__("negative_max", 1.0),
        lambda p: p["provenance"]["decision_policy"]["Mg"].__setitem__(
            "units", "element_wt_percent"
        ),
        lambda p: p["provenance"]["decision_policy"]["Na"].__setitem__("positive_min", True),
    ],
)
def test_rehashed_invalid_artifact_rejected(tmp_path, spectral_fixture, change):
    path = tmp_path / "profile.json"
    save_model(path, spectral_fixture)
    payload = json.loads(path.read_text())
    payload.pop("identity")
    change(payload)
    payload["identity"] = digest(payload)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        LineAdapterV3.load(path)


def test_identity_duplicate_key_and_invalid_spectrum_rejected(tmp_path, spectral_fixture):
    path = tmp_path / "profile.json"
    model = save_model(path, spectral_fixture)
    payload = json.loads(path.read_text())
    payload["thresholds"][0] = 0.3
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="integrity"):
        LineAdapterV3.load(path)
    path.write_text('{"schema_version":3,"schema_version":3}')
    with pytest.raises(ValueError, match="Duplicate"):
        LineAdapterV3.load(path)
    for invalid in (np.zeros((1, len(GRID))), np.full((1, len(GRID)), np.nan)):
        with pytest.raises(ValueError):
            model.predict_spectra(invalid, GRID)
    plan = copy.deepcopy(model.plan)
    plan["variant"] = "continuum-snr-neutral"
    with pytest.raises(ValueError, match="stages"):
        extract_features(spectral_fixture[1], GRID, plan)


def test_bounded_no_follow_loading(tmp_path, spectral_fixture):
    path = tmp_path / "profile.json"
    save_model(path, spectral_fixture)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="safely open"):
        LineAdapterV3.load(link)
    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(ValueError, match="Invalid artifact"):
        LineAdapterV3.load(path)
    path.write_text('{"identity":NaN}')
    with pytest.raises(ValueError, match="Nonfinite"):
        LineAdapterV3.load(path)
