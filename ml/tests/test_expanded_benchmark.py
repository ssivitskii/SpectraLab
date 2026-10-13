from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import f1_score
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation.robustness import (
    CachedRenderer,
    Composition,
    Profile,
    RobustnessConfig,
    compositions,
    focused_catalog,
    grouped_counts,
    hierarchical_bootstrap,
    metrics_from_counts,
    summarize_counts,
)
from spectralab_ml.simulation import GenerationConfig, generate_spectrum


@pytest.fixture
def protocol():
    path = Path(__file__).resolve().parents[1] / "configs/expanded_benchmark.yaml"
    config = RobustnessConfig.load(path)
    source = ReferenceCatalog.from_cache(path.parent / config.reference_cache)
    return config, source


def test_protocol_fixes_real_close_lines_and_normalized_contributions(protocol):
    config, source = protocol
    assert len(config.seeds) == 5 and config.base_samples > 48
    close = config.suites[1]
    catalog = focused_catalog(source, close)
    assert len(catalog.lines) == 2
    assert catalog.lines[1].wavelength_nm - catalog.lines[0].wavelength_nm == pytest.approx(0.00551)
    assert all(line.relative_intensity == 1 for line in catalog.lines)
    assert all(line.source_fields for line in catalog.lines)
    assert catalog.metadata["parent_content_sha256"] == source.metadata["content_sha256"]
    assert 0 in config.trace_ratios


@pytest.mark.parametrize(
    "profile", [Profile("g", "gaussian", 0.01), Profile("v", "voigt", 0.01, 0.01)]
)
def test_vector_renderer_matches_production_profile_and_intrinsic_variation(protocol, profile):
    config, source = protocol
    suite = replace(config.suites[1], snr_db=120)
    catalog = focused_catalog(source, suite)
    renderer = CachedRenderer(catalog, suite, 0.001)
    composition = Composition("one", (1.0, 0.3), 17, (29,), 0.0002)
    result = renderer.render([composition], profile)
    production = generate_spectrum(
        GenerationConfig(
            elements=suite.elements,
            component_weights=composition.weights,
            wavelength_start_nm=suite.wavelength_range_nm[0],
            wavelength_end_nm=suite.wavelength_range_nm[1],
            sampling_step_nm=0.001,
            instrumental_fwhm_nm=profile.instrumental_fwhm_nm,
            profile=profile.profile,
            lorentzian_fwhm_nm=profile.lorentzian_fwhm_nm,
            calibration_shift_nm=composition.shift_nm,
            seed=composition.intrinsic_seed,
            snr_db=None,
        ),
        catalog,
        class_order=suite.elements,
    )
    expected = renderer.processor.transform(production.wavelength_nm, production.intensity)
    np.testing.assert_allclose(result["X"][0], expected, atol=1e-5)
    assert result["y"].tolist() == [[1, 1]]


def test_trace_absence_is_a_separate_negative_control_and_groups_are_matched(protocol):
    config, source = protocol
    suite = config.suites[1]
    renderer = CachedRenderer(focused_catalog(source, suite), suite, 0.01)
    definitions = compositions(suite, 7, 8, 2, trace=True)
    profile = Profile("reference", "gaussian", suite.training_fwhm_nm)
    absent = renderer.render(definitions, profile, 0)
    weak = renderer.render(definitions, profile, 0.01)
    np.testing.assert_array_equal(absent["base_ids"], weak["base_ids"])
    np.testing.assert_array_equal(weak["base_ids"][::2], weak["base_ids"][1::2])
    assert absent["y"].sum() == 16 and weak["y"].sum() == 32
    assert weak["realized_trace_ratio"]["mean"] == pytest.approx(0.01, rel=0.25)
    assert len({d.weights for d in definitions}) == len(definitions)


def test_clustered_statistics_match_metrics_and_do_not_inflate_noise_replicates():
    truth = np.array([[1, 0], [1, 0], [0, 1], [0, 1], [1, 1], [1, 1]])
    predicted = np.array([[1, 1], [1, 0], [0, 0], [0, 1], [1, 1], [0, 1]])
    ids = np.array(["a", "a", "b", "b", "c", "c"])
    counts, groups = grouped_counts(truth, predicted, ids, np.full(6, -1))
    assert groups == ["a", "b", "c"] and len(counts) == 3
    point = metrics_from_counts(counts.sum(axis=0), 2)
    assert point["micro_f1"] == pytest.approx(f1_score(truth, predicted, average="micro"))
    assert point["macro_f1"] == pytest.approx(f1_score(truth, predicted, average="macro"))
    doubled, doubled_ids = grouped_counts(
        np.repeat(truth, 2, axis=0),
        np.repeat(predicted, 2, axis=0),
        np.repeat(ids, 2),
        np.full(12, -1),
    )
    assert doubled_ids == groups
    a = metrics_from_counts(hierarchical_bootstrap([counts] * 5, 300, 7), 2)
    b = metrics_from_counts(hierarchical_bootstrap([doubled] * 5, 300, 7), 2)
    np.testing.assert_array_equal(a["micro_f1"], b["micro_f1"])
    result = summarize_counts([counts] * 5, ("H", "He"), 300, 0.95, 7)
    assert result["micro_f1"]["pooled_estimate"] == pytest.approx(point["micro_f1"])
    assert result["trace_recall"]["mean"] is None
    assert result["micro_f1"]["std"] == 0


def test_paired_bootstrap_same_predictions_has_zero_difference():
    counts = np.array(
        [[1, 0, 0, 1, 0, 1, 1, 2, 0, 0, 0, 0], [0, 2, 0, 0, 1, 0, 1, 2, 0, 0, 0, 0]], dtype=float
    )
    a = hierarchical_bootstrap([counts] * 5, 300, 51)
    b = hierarchical_bootstrap([counts.copy()] * 5, 300, 51)
    np.testing.assert_array_equal(
        metrics_from_counts(a, 2)["micro_f1"] - metrics_from_counts(b, 2)["micro_f1"], np.zeros(300)
    )
