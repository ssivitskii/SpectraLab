import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
import yaml
from spectralab_ml.benchmark import measure_inference
from spectralab_ml.config import (
    ExperimentConfig,
    ProjectConfig,
    load_experiment_config,
    load_project_config,
    load_reference_catalog,
    repository_root,
)
from spectralab_ml.experiments import aggregate_series, run_experiment, run_series
from spectralab_ml.training import calibration_partition


def test_all_checked_in_configs_are_versioned_and_valid():
    for path in (repository_root() / "ml/configs/experiments").glob("*.yaml"):
        config = load_experiment_config(path)
        project = load_project_config(
            path.parent / config.project_config if config.project_config else None
        )
        config.validate_project(project)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"base_sampels": 18},
        {"seed": "7"},
        {"seed": True},
        {"seed": -1},
        {"variations": 1.0},
        {"base_samples": 5},
        {"models": ["typo"]},
        {"models": ["baseline-nnls"] * 2},
        {"factor": "sampling_step_nm", "values": [0.17]},
        {"factor": "sampling_step_nm", "values": ["clean"]},
        {"factor": "lorentzian_fwhm_nm", "values": [0.5]},
        {"generation": {"backgroud_level": 1}},
        {"generation": []},
        {"generation": {"background_level": "1"}},
        {"generation": {"background_level": None}},
        {"generation": {"ion_stage": 2}},
        {"base_samples": 10000, "variations": 100},
        {"seeds": [7, 7]},
        {"values": [20, 20]},
        {"values": [True]},
        {"timing_repeats": 1},
        {"elements": ["H", "Unknown"]},
    ],
)
def test_invalid_config_never_creates_run(tmp_path, change):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, **change}))
    with pytest.raises(ValueError):
        run_experiment(path, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_missing_version_and_project_types():
    with pytest.raises(ValueError, match="schema_version"):
        ExperimentConfig.from_mapping({"seed": 42})
    raw = yaml.safe_load((repository_root() / "ml/configs/project.yaml").read_text())
    for change in (
        {"model_step_nm": "0.05"},
        {"model_step_nm": 0.17},
        {"wavelength_range_nm": [800, 350]},
        {"ion_stage": True},
        {"wavelength_medium": "air"},
        {"elements": ["H", "H"]},
    ):
        with pytest.raises(ValueError):
            ProjectConfig.from_mapping({**raw, **change})


def test_reference_selection_integrity_classes_and_medium(tmp_path, catalog):
    path = tmp_path / "cache.json"
    from spectralab_ml.data.reference import ReferenceCatalog

    ReferenceCatalog(catalog.lines, dict(catalog.metadata)).to_cache(path)
    project = load_project_config()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    selected = load_reference_catalog(project, path, digest)
    assert selected.metadata["content_sha256"]
    with pytest.raises(ValueError, match="checksum"):
        load_reference_catalog(project, path, "0" * 64)
    with pytest.raises(ValueError, match="classes"):
        load_reference_catalog(project, path, class_order=("H", "Li"))
    with pytest.raises(ValueError, match="medium"):
        load_reference_catalog(replace(project, wavelength_medium="air"), path)
    payload = json.loads(path.read_text())
    payload["lines"][0]["wavelength_nm"] += 0.01
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=r"integrity|checksum"):
        load_reference_catalog(project, path)


def test_saved_nist_exports_have_reproducible_checksums_and_coverage():
    directory = repository_root() / "data/reference/nist_neutral_350_800"
    catalog = load_reference_catalog(load_project_config(), directory / "catalog.json")
    assert catalog.source_name == "nist_asd_saved_export"
    for source in catalog.metadata["sources"]:
        assert (
            hashlib.sha256((directory / source["raw_file"]).read_bytes()).hexdigest()
            == source["checksum_sha256"]
        )
    assert {line.element for line in catalog.lines} == set(load_project_config().elements)
    assert len(catalog.lines) > 100


def _result(seed, score=0.5):
    rows = []
    for model in ("baseline-nnls", "logistic-ovr"):
        for value in (10.0, 30.0):
            rows.append(
                {
                    "model_id": model,
                    "factor": "snr_db",
                    "value": value,
                    "test_base_sample_ids": [f"controlled-{seed}-0"],
                    "metrics": {
                        "micro_f1": score,
                        "macro_f1": score,
                        "exact_match_accuracy": score,
                        "hamming_loss": 1 - score,
                        "inference_ms_per_spectrum": 0.1,
                        "single": {"count": 0, "micro_f1": None, "exact_match_accuracy": None},
                        "mixture": {"count": 1, "micro_f1": score, "exact_match_accuracy": score},
                    },
                }
            )
    return {
        "seed": seed,
        "run_id": f"run-{seed}",
        "status": "completed",
        "runs": rows,
        "protocol_hash": "protocol",
        "source_hash": "source",
        "config_hash": str(seed),
    }


def _aggregate(rows, seeds):
    config = ExperimentConfig.from_mapping(
        {"schema_version": 1, "models": ["baseline-nnls", "logistic-ovr"], "values": [10, 30]}
    )
    return aggregate_series(rows, seeds, config, protocol_hash="protocol", source_hash="source")


def test_paired_aggregation_excludes_failed_partial_unmatched_and_mixed_protocols():
    rows = [_result(1, 0.2), _result(2, 0.8), _result(3), _result(4), _result(5), _result(6)]
    rows[2].update(status="failed", error="deliberate test failure")
    rows[3]["runs"].pop()
    rows[4]["runs"][0]["test_base_sample_ids"] = ["different"]
    rows[5]["protocol_hash"] = "incompatible"
    result = _aggregate(rows, [1, 2, 3, 4, 5, 6, 7])
    assert result["successful_seeds"] == [1, 2]
    assert [r["seed"] for r in result["failed_runs"]] == [3]
    assert [r["seed"] for r in result["incomplete_runs"]] == [4, 5, 6, 7]
    metric = result["aggregates"][0]["metrics"]["micro_f1"]
    assert metric["mean"] == pytest.approx(0.5)
    assert metric["std"] == pytest.approx(np.std([0.2, 0.8], ddof=1))
    assert metric["count"] == 2
    assert result["aggregates"][0]["metrics"]["single"]["micro_f1"] == {
        "mean": None,
        "std": None,
        "count": 0,
    }
    assert _aggregate([_result(1)], [1])["aggregates"][0]["metrics"]["micro_f1"]["std"] is None
    assert _aggregate([], [1])["aggregates"][0]["metrics"]["micro_f1"]["mean"] is None
    duplicate = _aggregate([_result(1), _result(1)], [1])
    assert duplicate["successful_seeds"] == []


def test_timing_discards_warmup_and_retains_repeated_observations(monkeypatch):
    calls = []
    clock = iter([0, 2_000_000, 3_000_000, 9_000_000, 10_000_000, 14_000_000])
    monkeypatch.setattr("spectralab_ml.benchmark.time.perf_counter_ns", lambda: next(clock))
    result = measure_inference(lambda: calls.append(True), 2, warmups=2, repeats=3)
    assert len(calls) == 5
    assert result["timing"]["observations"] == [1, 3, 2]
    assert result["timing"]["median"] == 2
    assert result["timing"]["p95"] == pytest.approx(2.9)


def test_development_partitions_do_not_leak_variations_or_test(dataset):
    calibration, thresholds, metadata = calibration_partition(dataset, 7)
    assert np.all(calibration ^ thresholds)
    calibration_ids = set(metadata["calibration_base_sample_ids"])
    threshold_ids = set(metadata["threshold_base_sample_ids"])
    assert not calibration_ids & threshold_ids
    assert calibration_ids | threshold_ids == set(dataset.validation.base_sample_ids)
    assert not (calibration_ids | threshold_ids) & set(dataset.test.base_sample_ids)
    for mask, ids in ((calibration, calibration_ids), (thresholds, threshold_ids)):
        assert all(
            selected == (sample in ids)
            for selected, sample in zip(mask, dataset.validation.base_sample_ids, strict=True)
        )


def test_series_records_failed_seed_without_zeros(tmp_path, monkeypatch):
    import spectralab_ml.experiments as module

    config = tmp_path / "series.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "seeds": [1, 2, 3],
                "models": ["baseline-nnls", "logistic-ovr"],
                "values": [10, 30],
            }
        )
    )
    _, _, _, hashes = module._prepare(config, load_experiment_config(config))

    def fake_run(path, output_root, **kwargs):
        seed = kwargs["seed"]
        if seed == 2:
            raise RuntimeError("intentional benchmark failure")
        directory = output_root / f"run-{seed}"
        directory.mkdir(parents=True)
        row = _result(seed, seed / 10)
        row.update(protocol_hash=hashes["protocol_hash"], source_hash=hashes["source_hash"])
        (directory / "result.json").write_text(json.dumps(row))
        return directory

    monkeypatch.setattr(module, "run_experiment", fake_run)
    output = run_series(config, tmp_path / "series")
    report = json.loads((output / "result.json").read_text())
    assert report["status"] == "incomplete"
    assert report["expected_seeds"] == [1, 2, 3]
    assert report["successful_seeds"] == [1, 3]
    assert report["failed_runs"][0]["error"] == "intentional benchmark failure"
    assert len(report["seed_runs"]) == 3
    assert report["aggregates"][0]["metrics"]["micro_f1"]["mean"] == pytest.approx(0.2)
    originals = [json.loads(p.read_text()) for p in (output / "runs").glob("*/result.json")]
    failed = {**report["seed_runs"][1]}
    recalculated = module.aggregate_series(
        [*originals, failed],
        report["expected_seeds"],
        load_experiment_config(config),
        protocol_hash=report["protocol_hash"],
        source_hash=report["source_hash"],
    )
    assert recalculated["aggregates"] == report["aggregates"]


def test_cnn_artifact_registry_and_corrupt_checkpoint(tmp_path, catalog, classes):
    pytest.importorskip("torch")
    from spectralab_ml.datasets import build_demo_dataset
    from spectralab_ml.inference.predictor import ModelRegistry
    from spectralab_ml.models import load_artifact
    from spectralab_ml.models.cnn import CNN1D
    from spectralab_ml.preprocessing import SpectrumPreprocessor
    from spectralab_ml.training import train_and_save_models

    processor = SpectrumPreprocessor(model_step_nm=1.0)
    data = build_demo_dataset(catalog, processor, classes, seed=17, base_samples=18, variations=1)
    train_and_save_models(
        data,
        catalog,
        processor,
        tmp_path,
        seed=17,
        model_ids=["cnn-1d"],
        timing_warmups=1,
        timing_repeats=2,
    )
    directory = tmp_path / "cnn-1d"
    model, loaded_processor, manifest = load_artifact(
        directory, expected_class_order=classes, expected_reference=catalog.metadata
    )
    assert isinstance(model, CNN1D)
    assert loaded_processor == processor
    assert manifest.artifact_format == "cnn-state-npz-v1"
    assert manifest.seed == 17
    assert not (directory / "model.joblib").exists()
    registry = ModelRegistry(tmp_path, catalog, classes, processor)
    available = {m["model_id"]: m for m in registry.list_models()}
    assert available["cnn-1d"]["ready"]
    sample = next(r for r in data.records if r["split"] == "test")
    prediction = registry.predict(
        "cnn-1d", np.asarray(sample["wavelength_nm"]), np.asarray(sample["intensity"])
    )
    expected = model.scores(
        processor.transform(sample["wavelength_nm"], sample["intensity"])[None]
    )[0]
    np.testing.assert_allclose([row["value"] for row in prediction.scores], expected)
    checkpoint = directory / "model.npz"
    checkpoint.write_bytes(checkpoint.read_bytes() + b"corrupt")
    unavailable = {m["model_id"]: m for m in registry.list_models()}["cnn-1d"]
    assert not unavailable["ready"]
    assert unavailable["diagnostic_code"] == "artifact_incompatible"
    assert str(tmp_path) not in json.dumps(unavailable)


def test_registry_starts_without_importing_optional_runtime(tmp_path):
    import subprocess
    import sys

    script = """
import builtins
import importlib.util
import sys
from pathlib import Path
original_import = builtins.__import__
original_spec = importlib.util.find_spec
def guarded_import(name, *args, **kwargs):
    if name == "torch" or name.startswith("torch."):
        raise ImportError("optional runtime intentionally absent")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def guarded_spec(name, *args, **kwargs):
    return None if name == "torch" else original_spec(name, *args, **kwargs)
importlib.util.find_spec = guarded_spec
from spectralab_ml.data.reference import load_demo_catalog
from spectralab_ml.inference.predictor import ModelRegistry
from spectralab_ml.preprocessing import SpectrumPreprocessor
registry = ModelRegistry(
    Path(sys.argv[1]), load_demo_catalog(), ("H", "He", "Na", "Mg", "Ca", "Fe"),
    SpectrumPreprocessor(model_step_nm=1))
models = {m["model_id"]: m for m in registry.list_models()}
assert models["builtin-nnls"]["ready"]
assert not models["cnn-1d"]["ready"]
assert models["cnn-1d"]["diagnostic_code"] == "optional_runtime_missing"
assert "torch" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stderr


def test_reference_version_roundtrips_dataset_model_and_run(tmp_path, catalog):
    from spectralab_ml.data.reference import ReferenceCatalog

    cache = tmp_path / "reference.json"
    local = ReferenceCatalog(catalog.lines, {**catalog.metadata, "source_version": "test-v1"})
    local.to_cache(cache)
    project = yaml.safe_load((repository_root() / "ml/configs/benchmark_project.yaml").read_text())
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(project))
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "project_config": "project.yaml",
                "reference_cache": "reference.json",
                "reference_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
                "models": ["baseline-nnls"],
                "base_samples": 18,
                "variations": 1,
                "values": [20],
                "timing_warmups": 1,
                "timing_repeats": 2,
            }
        )
    )
    directory = run_experiment(config, tmp_path / "runs")
    result = json.loads((directory / "result.json").read_text())
    dataset = json.loads((directory / "dataset/metadata.json").read_text())
    model = json.loads((directory / "models/baseline-nnls/manifest.json").read_text())
    assert result["reference_metadata"] == dataset["reference"] == model["reference_metadata"]
    assert model["reference_metadata"]["source_version"] == "test-v1"
    assert result["source_hash"] and result["config_hash"]
    assert result["runs"][0]["metrics"]["timing"]["repeats"] == 2


def test_duplicate_yaml_fields_and_project_relative_cache(tmp_path, catalog):
    from spectralab_ml.data.reference import ReferenceCatalog

    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 1\nseed: 7\nseed: 17\n")
    with pytest.raises(ValueError, match="Duplicate YAML"):
        run_experiment(path, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()
    cache = tmp_path / "cache.json"
    ReferenceCatalog(catalog.lines, dict(catalog.metadata)).to_cache(cache)
    raw = yaml.safe_load((repository_root() / "ml/configs/project.yaml").read_text())
    raw["reference_cache"] = "cache.json"
    path.write_text(yaml.safe_dump(raw))
    selected = load_reference_catalog(load_project_config(path))
    assert selected.metadata["content_sha256"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_step_nm", 0),
        ("model_step_nm", float("inf")),
        ("model_step_nm", 1e-10),
        ("wavelength_start_nm", float("nan")),
        ("wavelength_end_nm", 300),
        ("grid_point_count", 10**9),
        ("grid_point_count", 451.0),
        ("preprocessing.model_step_nm", 0),
        ("preprocessing.model_step_nm", float("inf")),
        ("preprocessing.normalization", "invalid"),
        ("preprocessing.normalization", None),
    ],
)
def test_corrupt_model_grid_is_unavailable_without_crashing_registry(
    tmp_path,
    catalog,
    classes,
    field,
    value,
):
    from spectralab_ml.inference.predictor import ModelRegistry
    from spectralab_ml.models import BaselineNNLS, ModelManifest, save_artifact
    from spectralab_ml.preprocessing import SpectrumPreprocessor

    processor = SpectrumPreprocessor(model_step_nm=1)
    estimator = BaselineNNLS(np.eye(len(classes), len(processor.model_grid)))
    manifest = ModelManifest.create(
        model_id="baseline-nnls",
        model_type=type(estimator).__name__,
        class_order=classes,
        preprocessor=processor,
        thresholds=dict.fromkeys(classes, 0.2),
        threshold_selection={"split": "validation"},
        score_kind=estimator.score_kind,
        calibrated=False,
        dataset_hash="test",
        split_hash="test",
        seed=7,
        metrics={},
        reference_metadata=catalog.metadata,
        generator_parameters={},
        split_ids={},
    )
    directory = tmp_path / "baseline-nnls"
    save_artifact(directory, estimator, processor, manifest)
    path = directory / "manifest.json"
    raw = json.loads(path.read_text())
    if field.startswith("preprocessing."):
        raw["preprocessing"][field.split(".")[1]] = value
    else:
        raw[field] = value
    path.write_text(json.dumps(raw))
    registry = ModelRegistry(tmp_path, catalog, classes, processor)
    entries = {row["model_id"]: row for row in registry.list_models()}
    assert entries["builtin-nnls"]["ready"]
    assert not entries["baseline-nnls"]["ready"]
    assert entries["baseline-nnls"]["diagnostic_code"] == "artifact_incompatible"
    assert str(tmp_path) not in json.dumps(entries)


def test_cnn_artifact_preserves_training_configuration_on_reload_and_resave(tmp_path, catalog):
    pytest.importorskip("torch")
    from spectralab_ml.models import ModelManifest, load_artifact, save_artifact
    from spectralab_ml.models.cnn import CNN1D
    from spectralab_ml.preprocessing import SpectrumPreprocessor

    processor = SpectrumPreprocessor(model_step_nm=50)
    rng = np.random.default_rng(7)
    X = rng.random((8, len(processor.model_grid)))
    y = np.tile([[1, 0], [0, 1]], (4, 1))
    model = CNN1D(seed=7, epochs=2, learning_rate=0.0123).fit(X, y)
    manifest = ModelManifest.create(
        model_id="cnn-1d",
        model_type="CNN1D",
        class_order=("H", "He"),
        preprocessor=processor,
        thresholds={"H": 0, "He": 0},
        threshold_selection={"split": "validation"},
        score_kind=model.score_kind,
        calibrated=False,
        dataset_hash="test",
        split_hash="test",
        seed=7,
        metrics={},
        reference_metadata=catalog.metadata,
        generator_parameters={},
        split_ids={},
    )
    directory = tmp_path / "cnn-1d"
    save_artifact(directory, model, processor, manifest)
    loaded, pipeline, saved = load_artifact(directory)
    assert loaded.epochs == 2 and loaded.learning_rate == 0.0123
    np.testing.assert_array_equal(model.scores(X), loaded.scores(X))
    copy = tmp_path / "copy"
    save_artifact(copy, loaded, pipeline, saved)
    _, _, copied_manifest = load_artifact(copy)
    assert copied_manifest.model_configuration == saved.model_configuration
    path = copy / "manifest.json"
    original = json.loads(path.read_text())
    for field, value in (
        ("architecture", "unrecognized-v2"),
        ("epochs", True),
        ("learning_rate", float("nan")),
        ("seed", 19),
    ):
        altered = json.loads(json.dumps(original))
        altered["model_configuration"][field] = value
        path.write_text(json.dumps(altered))
        with pytest.raises(ValueError):
            load_artifact(copy)
    original.pop("model_configuration")
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="model_configuration"):
        load_artifact(copy)
