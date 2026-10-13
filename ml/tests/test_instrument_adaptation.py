from __future__ import annotations

import hashlib
import json
import shutil

import numpy as np
import pytest
from spectralab_ml.models.instrument_adaptation import InstrumentAdapter


@pytest.fixture
def training():
    grid = np.round(np.arange(350, 800.01, 0.1), 8)
    classes = ("Na", "Mg", "Ca", "Fe")
    y = np.array([[bool(i & (1 << k)) for k in range(4)] for i in range(16)])
    X = np.full((16, len(grid)), 0.1)
    for k, wavelength in enumerate((589.0, 383.5, 422.5, 438.5)):
        X[:, np.argmin(abs(grid - wavelength))] += 4 * y[:, k]
    kwargs = {
        "sample_ids": [f"physical-{i}" for i in range(len(X))],
        "grid_nm": grid,
        "observed_intervals": [[383, 468], [500, 800]],
        "class_order": classes,
        "instrument_id": "chemcam-laboratory",
        "C": 0.1,
        "seed": 41,
        "provenance": {
            "estimand": "operational_above_threshold",
            "oxide_policy": {"negative_max_wt_pct": 0.1, "positive_min_wt_pct": 1.0},
            "source": "test_fixture",
            "fit_split": "train_only",
        },
    }
    return X, y, np.ones_like(y, dtype=bool), kwargs


def test_safe_roundtrip_preserves_scores_and_pinned_contract(training, tmp_path):
    X, y, known, kwargs = training
    model = InstrumentAdapter().fit(X, y, known, **kwargs)
    before = model.scores(X)
    thresholds = dict.fromkeys(kwargs["class_order"], 0.6)
    thresholds["Mg"] = float(np.nextafter(1.0, np.inf))
    model.save(tmp_path / "model", thresholds=thresholds)
    restored = InstrumentAdapter.load(
        tmp_path / "model",
        expected_grid_nm=kwargs["grid_nm"],
        expected_observed_intervals=kwargs["observed_intervals"],
        expected_class_order=kwargs["class_order"],
        expected_instrument_id=kwargs["instrument_id"],
    )
    np.testing.assert_array_equal(restored.scores(X), before)
    assert restored.identity == model.identity
    assert model.manifest["feature_count"] == 385
    assert model.manifest["thresholds"]["Na"] == 0.6
    assert model.manifest["thresholds"]["Mg"] > 1
    assert not np.any(restored.scores(X)[:, 1] >= model.manifest["thresholds"]["Mg"])
    assert not model.calibrated
    assert {p.name for p in (tmp_path / "model").iterdir()} == {"manifest.json", "model.npz"}
    with pytest.raises(ValueError, match="already exists"):
        model.save(tmp_path / "model")
    for expected in (
        {"expected_instrument_id": "another-instrument"},
        {"expected_class_order": tuple(reversed(kwargs["class_order"]))},
        {"expected_grid_nm": kwargs["grid_nm"] + 0.1},
        {"expected_observed_intervals": [[384, 468], [500, 800]]},
    ):
        with pytest.raises(ValueError, match="incompatible"):
            InstrumentAdapter.load(tmp_path / "model", **expected)


def test_unknown_labels_and_replicate_counts_do_not_reweight_physical_samples(training):
    X, y, known, kwargs = training
    known[::3, 0] = False
    base = InstrumentAdapter().fit(X, y, known, **kwargs)
    changed_y = y.copy()
    changed_y[~known] = ~changed_y[~known]
    hidden = InstrumentAdapter().fit(X, changed_y, known, **kwargs)
    np.testing.assert_array_equal(base.coefficients, hidden.coefficients)
    # A sample with an unknown Na label gets seven times as many acquisitions.
    indices = np.concatenate([np.arange(len(X)), np.zeros(6, dtype=int)])
    repeated = InstrumentAdapter().fit(
        X[indices],
        y[indices],
        known[indices],
        **{**kwargs, "sample_ids": [kwargs["sample_ids"][i] for i in indices]},
    )
    np.testing.assert_allclose(repeated.mean, base.mean, rtol=0, atol=1e-12)
    # Different row order/count can change L-BFGS floating-point termination slightly.
    np.testing.assert_allclose(repeated.scores(X), base.scores(X), rtol=0, atol=1e-7)
    assert base.manifest["training"]["known_support"]["Na"]["unknown_rows"] == 6


def test_features_ignore_unobserved_gap_and_clip_negatives_without_zero_band_fallback(training):
    X, y, known, kwargs = training
    model = InstrumentAdapter().fit(X, y, known, **kwargs)
    probe = X.copy()
    probe[:, model.observed_mask == 0] = 1e200
    np.testing.assert_array_equal(model.scores(probe), model.scores(X))
    # Positive band scaling is removed independently, including extreme finite counts.
    probe[:, kwargs["grid_nm"] >= 500] *= 1000
    np.testing.assert_allclose(model.scores(probe), model.scores(X), atol=1e-12)
    index = np.argmin(abs(kwargs["grid_nm"] - 510.2))
    probe[:, index] = -1000
    # Other points in the same max-bin remain at the original positive intensity.
    np.testing.assert_allclose(model.scores(probe), model.scores(X), atol=1e-12)
    probe[:, kwargs["grid_nm"] >= 500] = -1
    with pytest.raises(ValueError, match="Every observed band"):
        model.scores(probe)


def test_fit_rejects_unsupported_training_and_preserves_scaler_during_scoring(training):
    X, y, known, kwargs = training
    model = InstrumentAdapter().fit(X, y, known, **kwargs)
    mean = model.mean.copy()
    model.scores(np.flip(X, axis=0))
    np.testing.assert_array_equal(model.mean, mean)
    bad_y = y.copy()
    bad_y[:, 0] = False
    with pytest.raises(ValueError, match="Both known label states"):
        InstrumentAdapter().fit(X, bad_y, known, **kwargs)
    with pytest.raises(ValueError, match="conflict within a physical sample"):
        InstrumentAdapter().fit(X, y, known, **{**kwargs, "sample_ids": ["one"] * len(X)})
    with pytest.raises(ValueError, match="Boolean labels"):
        InstrumentAdapter().fit(X, y.astype(float), known, **kwargs)
    with pytest.raises(ValueError, match="operational_above_threshold"):
        InstrumentAdapter().fit(X, y, known, **{**kwargs, "provenance": {}})
    with pytest.raises(ValueError, match="already fitted"):
        model.fit(X, y, known, **kwargs)
    with pytest.raises(ValueError, match=r"0\.1 nm grid"):
        InstrumentAdapter().fit(
            X, y, known, **{**kwargs, "grid_nm": kwargs["grid_nm"] + np.arange(X.shape[1]) * 0.1}
        )


def _rehash_manifest(directory, mutate=None):
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    if mutate:
        mutate(manifest)
    manifest["coefficients_sha256"] = hashlib.sha256(
        (directory / "model.npz").read_bytes()
    ).hexdigest()
    manifest["identity"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "identity"},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize(
    "mutation", ["nan", "shape", "mask", "object", "schema", "seed", "feature_type", "checksum"]
)
def test_load_rejects_tampered_or_incompatible_numeric_artifacts(training, tmp_path, mutation):
    X, y, known, kwargs = training
    directory = tmp_path / "adapter"
    InstrumentAdapter().fit(X, y, known, **kwargs).save(directory)
    if mutation == "checksum":
        with (directory / "model.npz").open("ab") as stream:
            stream.write(b"changed")
    elif mutation in {"schema", "seed", "feature_type"}:

        def mutate(manifest):
            if mutation == "schema":
                manifest["schema_version"] = 99
            elif mutation == "seed":
                manifest["seed"] = True
            else:
                manifest["feature_spec"]["version"] = True

        _rehash_manifest(directory, mutate)
    else:
        with np.load(directory / "model.npz", allow_pickle=False) as archive:
            arrays = dict(archive)
        if mutation == "nan":
            arrays["coefficients"][0, 0] = np.nan
        elif mutation == "shape":
            arrays["coefficients"] = arrays["coefficients"][:, :-1]
        elif mutation == "mask":
            arrays["observed_mask"][0] = 1
        else:
            arrays["coefficients"] = np.array([object()], dtype=object)
        np.savez_compressed(directory / "model.npz", **arrays)
        _rehash_manifest(directory)
    with pytest.raises(ValueError):
        InstrumentAdapter.load(directory)
    # A copied artifact remains bound to the original instrument, never its directory name.
    copied = tmp_path / "another-instrument"
    shutil.copytree(directory, copied)
    with pytest.raises(ValueError):
        InstrumentAdapter.load(copied, expected_instrument_id="another-instrument")
