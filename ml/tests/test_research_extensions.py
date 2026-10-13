import json
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.special import voigt_profile
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.data.wavelengths import air_to_vacuum, vacuum_to_air
from spectralab_ml.models.calibration import CalibratedEstimator, reliability_metrics
from spectralab_ml.models.cnn import CNN1D, cnn_available
from spectralab_ml.models.estimators import LogisticOVR
from spectralab_ml.models.ions import aggregate_ion_detections, make_ion_templates
from spectralab_ml.simulation import GenerationConfig, generate_spectrum
from spectralab_ml.simulation.generator import pixel_integrated_gaussian, pixel_integrated_voigt


def test_voigt_finite_window_and_gaussian_limit():
    grid = np.arange(350.0, 801.0)
    for width, gamma in [(0.01, 0.005), (0.5, 0.1), (2.0, 1.0)]:
        profile = pixel_integrated_voigt(grid, 350.49, width, gamma * 2)
        sigma = width / (2 * np.sqrt(2 * np.log(2)))
        expected = quad(
            lambda x, sigma=sigma, gamma=gamma: voigt_profile(x, sigma, gamma),
            -0.99,
            450.01,
            points=[0],
            epsabs=1e-10,
        )[0]
        assert profile.sum() == pytest.approx(expected, abs=1e-7)
        assert 0 < profile.sum() < 1
    np.testing.assert_array_equal(
        pixel_integrated_voigt(grid, 550.49, 0.5, 0), pixel_integrated_gaussian(grid, 550.49, 0.5)
    )


def test_detector_limits_noise_and_reproducibility(catalog, classes):
    config = GenerationConfig(
        elements=("Na",),
        component_weights=(1.0,),
        snr_db=None,
        amplitude_variation=0,
        noise_model="poisson_read",
        photon_scale=200,
        read_noise_std=0.02,
        background_level=2,
    )
    first = generate_spectrum(config, catalog, class_order=classes)
    np.testing.assert_array_equal(
        first.intensity, generate_spectrum(config, catalog, class_order=classes).intensity
    )
    residual = first.intensity - first.clean_signal - 2
    expected_variance = np.mean((first.clean_signal + 2) / 200) + 0.02**2
    assert np.var(residual) == pytest.approx(expected_variance, rel=0.08)
    clipped = generate_spectrum(replace(config, saturation_level=1), catalog, class_order=classes)
    assert clipped.intensity.max() <= 1
    assert clipped.parameters["noise_model"] == "poisson_read"
    with pytest.raises(ValueError, match="snr_db"):
        replace(config, snr_db=20).validate()
    with pytest.raises(ValueError, match="nonnegative"):
        generate_spectrum(replace(config, background_level=-1), catalog, class_order=classes)


def test_reference_cache_integrity_and_legacy(tmp_path, catalog):
    path = tmp_path / "reference.json"
    catalog.to_cache(path)
    assert len(ReferenceCatalog.from_cache(path).lines) == len(catalog.lines)
    payload = json.loads(path.read_text())
    payload["lines"][0]["wavelength_nm"] += 1
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        ReferenceCatalog.from_cache(path)
    payload["metadata"].pop("schema_version")
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="schema"):
        ReferenceCatalog.from_cache(path)


def test_air_vacuum_reference_and_roundtrip():
    # NIST ASD's documented boundary example: 200.0648 vacuum -> 200.0000 air.
    assert float(vacuum_to_air(200.0648)) == pytest.approx(200, abs=5e-5)
    grid = np.linspace(200, 1600, 100)
    np.testing.assert_allclose(air_to_vacuum(vacuum_to_air(grid)), grid, atol=1e-10)
    with pytest.raises(ValueError):
        air_to_vacuum(100)


def test_ion_templates_and_element_or(catalog, processor):
    neutral = next(line for line in catalog.lines if line.element == "Na")
    ion = replace(neutral, ion_stage=2, wavelength_nm=neutral.wavelength_nm + 3)
    mixed = ReferenceCatalog([neutral, ion], catalog.metadata)
    species = (("Na", 1), ("Na", 2))
    templates = make_ion_templates(mixed, processor, species)
    assert np.argmax(templates[0]) != np.argmax(templates[1])
    assert aggregate_ion_detections(np.array([[False, True]]), species, ("Na",)).tolist() == [
        [True]
    ]
    with pytest.raises(ValueError, match="boolean"):
        aggregate_ion_detections(np.array([[0.1, 0.7]]), species, ("Na",))


def test_calibration_metrics_and_roundtrip(tmp_path, dataset):
    import joblib

    base = LogisticOVR(seed=42).fit(dataset.train.X, dataset.train.y)
    model = CalibratedEstimator(base).fit_calibration(dataset.validation.X, dataset.validation.y)
    result = model.scores(dataset.test.X)
    assert np.all((result >= 0) & (result <= 1))
    assert model.calibration_metadata["scope"].startswith("synthetic")
    metrics = reliability_metrics(dataset.test.y, result)
    assert sum(row["count"] for row in metrics["bins"]) == result.size
    joblib.dump(model, tmp_path / "calibration.joblib")
    np.testing.assert_array_equal(
        result, joblib.load(tmp_path / "calibration.joblib").scores(dataset.test.X)
    )


@pytest.mark.skipif(not cnn_available(), reason="optional cnn runtime not installed")
def test_cnn_numeric_checkpoint_roundtrip(tmp_path):
    rng = np.random.default_rng(7)
    features = rng.random((12, 64))
    labels = rng.integers(0, 2, (12, 3))
    model = CNN1D(seed=7, epochs=2).fit(features, labels)
    path = tmp_path / "model.npz"
    model.save_checkpoint(path)
    loaded = CNN1D.load_checkpoint(path, seed=7, input_points=64, class_count=3)
    np.testing.assert_array_equal(model.scores(features), loaded.scores(features))
    with pytest.raises(ValueError, match="shape"):
        CNN1D.load_checkpoint(path, seed=7, input_points=64, class_count=4)


def test_open_set_rejection_is_separate_from_detection():
    from spectralab_ml.evaluation.openset import ResidualRejector, rejection_metrics

    templates = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    known_dev = np.array([[1, 0, 0.02], [0, 1, 0.03], [1, 1, 0.01]])
    policy = ResidualRejector(templates).fit_known_development(known_dev)
    threshold = policy.threshold
    rejected = policy.abstain(np.array([[0, 0, 1.0], [1, 0, 0.01]]))
    assert rejected.tolist() == [True, False]
    assert policy.threshold == threshold
    assert rejection_metrics(rejected[:1], known=False)["unknown_false_acceptance_rate"] == 0
    assert rejection_metrics(rejected[1:], known=True)["known_false_rejection_rate"] == 0


def test_external_admission_requires_evidence_and_disjoint_samples(tmp_path):
    import hashlib

    from spectralab_ml.evaluation.external import inspect_external_test

    csv = tmp_path / "spectrum.csv"
    csv.write_text("wave,intensity\n350,1\n575,2\n800,1\n")
    manifest = {
        "schema_version": 1,
        "units": "nm",
        "samples": [
            {
                "sample_id": "pellet-1",
                "session_id": "session-1",
                "filename": csv.name,
                "sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
                "labels": {"H": None, "Na": True},
            }
        ],
    }
    result = inspect_external_test(manifest, tmp_path)
    assert result["status"] == "blocked" and result["metrics"] is None
    assert "wavelength_medium_not_documented" in result["blockers"]
    assert result["samples"][0]["unknown_labels"] == ["H"]
    with pytest.raises(ValueError, match="samples overlap"):
        inspect_external_test({**manifest, "development_sample_ids": ["pellet-1"]}, tmp_path)
    with pytest.raises(ValueError, match="sessions overlap"):
        inspect_external_test({**manifest, "development_session_ids": ["session-1"]}, tmp_path)
