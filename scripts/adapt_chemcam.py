"""Lock, fit and evaluate the ChemCam operational instrument profile once per run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from spectralab_ml.config import repository_root
from spectralab_ml.data.chemcam_measurements import read_chemcam_ccs
from spectralab_ml.data.reference import ReferenceCatalog
from spectralab_ml.evaluation.adaptation_protocol import (
    compare_detections,
    guard_development_fit,
    validate_protocol,
)
from spectralab_ml.evaluation.instrument_selection import (
    grouped_development_split,
    select_operational_thresholds,
)
from spectralab_ml.evaluation.real_validation import (
    interpolate_observed,
    normalize_observed,
    observation_mask,
)
from spectralab_ml.evaluation.test_lock import consume_test
from spectralab_ml.models import BaselineNNLS
from spectralab_ml.models.instrument_adaptation import InstrumentAdapter
from spectralab_ml.preprocessing import SpectrumPreprocessor
from spectralab_ml.training import make_reference_templates

CLASSES = ["Na", "Mg", "Ca", "Fe"]
OXIDES = dict(zip(CLASSES, ["Na2O", "MgO", "CaO", "FeOT"], strict=True))
SEED = 20261013


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def freeze(root: Path, destination: Path) -> dict:
    """Only metadata, evidence and split labels are accessed before this lock."""
    intake_path = root / "data/reference/chemcam_adaptation_intake.json"
    intake = json.loads(intake_path.read_text())
    amendment_path = root / "data/reference/chemcam_adaptation_source_amendments.json"
    amendment = json.loads(amendment_path.read_text()) if amendment_path.exists() else None
    excluded = set()
    if amendment is not None:
        if amendment.get("locked_before_predictions") is not True:
            raise ValueError("Source exclusions must be reviewed before model predictions")
        for exclusion in amendment["exclusions"]:
            if sha(root / exclusion["evidence_path"]) != exclusion["evidence_sha256"]:
                raise ValueError("Missing-source evidence changed")
            excluded.add(exclusion["measurement_id"])
    if not excluded <= {r["measurement_id"] for r in intake["records"]}:
        raise ValueError("Amendment contains an unknown measurement")
    for record in intake["records"]:
        expected = "download_failed" if record["measurement_id"] in excluded else "downloaded"
        if record["status"] != expected:
            raise ValueError("All selected files must be downloaded or explicitly source-excluded")
    included = [r for r in intake["records"] if r["measurement_id"] not in excluded]
    for relative, expected in intake["pinned_inputs"].items():
        if sha(root / relative) != expected:
            raise ValueError(f"Intake metadata checksum changed: {relative}")
    source = intake["provenance"]
    if source["license_id"] != "us-pd" or source["wavelength_medium"] != "vacuum":
        raise ValueError("Confirmed license and vacuum axis are required")
    chemistry_path = "data/raw/chemcam_external/ccam_calibration_compositions.csv"
    chemistry_sha = intake["pinned_inputs"][chemistry_path]
    reference_path = root / "data/reference/nist_neutral_350_800/catalog.json"
    reference = ReferenceCatalog.from_cache(reference_path)
    if reference.metadata.get("ion_stage") != 1:
        raise ValueError("Frozen baseline requires the neutral NIST reference")

    def normalize(value: str) -> str:
        return re.sub("[^a-z0-9]", "", value.lower())

    sample_names = Counter(normalize(item["composition_row"]["Sample Name"]) for item in included)
    records = []
    for item in included:
        record = {**item, "labels": {}}
        sample_name = normalize(item["composition_row"]["Sample Name"])
        if sample_name and sample_names[sample_name] > 1:
            record["physical_sample_id"] = "published-sample-" + sample_name
            record["family_id"] = record["physical_sample_id"]
            record["physical_grouping_basis"] = (
                "Identical published Sample Name; repeated targets count as one physical material."
            )
        if record["physical_sample_id"] in {"sanci", "sanck"}:
            record["family_id"] = "sanc-alteration-suite"
            record["family_grouping_basis"] = "Clegg2017 shared SanC alteration suite."
        if re.fullmatch(r"mix[1-6][a-z]", record["physical_sample_id"]):
            record["family_id"] = "synthetic-mix-series"
            record["family_grouping_basis"] = (
                "Conservative joint group for synthetic MIX series; Clegg2017 describes "
                "shared K1919/oxide constituents. No related series crosses train/tune."
            )
        for element, oxide in OXIDES.items():
            raw = item["composition_row"][oxide].strip()
            value = float(raw) if raw else None
            if value is not None and not np.isfinite(value):
                raise ValueError("Nonfinite independent composition")
            known = value is not None and (0 < value <= 0.1 or value >= 1)
            record["labels"][element] = {
                "state": "positive" if known and value >= 1 else "negative" if known else "unknown",
                "basis": "independent_composition" if known else "unknown",
                "source": source["composition_url"],
                "source_sha256": chemistry_sha,
                "statement": (
                    f"Published {oxide} assay, CSV row {item['composition_row_number']}; "
                    "zero/missing/guard band is unknown, not absence."
                ),
                "value": value,
                "units": "oxide_wt_percent",
                "oxide": oxide,
            }
        records.append(record)
    old = json.loads((root / "data/reference/chemcam_external_protocol.json").read_text())

    development = [r for r in records if r["role"] == "development"]
    protocol = {
        "schema_version": 1,
        "protocol_id": "chemcam-operational-instrument-adaptation-v1",
        "locked_at": datetime.now(UTC).isoformat(),
        "locked_before_predictions": True,
        "class_order": CLASSES,
        "estimand": "operational_above_threshold",
        "decision_policy": {
            element: {
                "oxide": oxide,
                "units": "oxide_wt_percent",
                "negative_max": 0.1,
                "positive_min": 1.0,
                "nominal_decision_level": 0.5,
                "zero_policy": "unknown",
            }
            for element, oxide in OXIDES.items()
        },
        "development": development,
        "test": [r for r in records if r["role"] == "test"],
        "previously_seen": {
            "measurement_ids": [],
            "physical_sample_ids": [normalize(s) for s in old["test_sample_ids"]],
            "session_ids": [old["acquisition_session_id"]],
            "input_sha256s": list(old["input_sha256"].values()),
        },
        "development_split": grouped_development_split(development, CLASSES, SEED),
        "intake_path": str(intake_path.relative_to(root)),
        "intake_sha256": sha(intake_path),
        "source_amendment": (
            {
                "path": str(amendment_path.relative_to(root)),
                "sha256": sha(amendment_path),
                "content": amendment,
            }
            if amendment is not None
            else None
        ),
        "reference": {
            "path": str(reference_path.relative_to(root)),
            "sha256": sha(reference_path),
            "metadata": reference.metadata,
        },
        "source": source,
        "preprocessing": {
            "grid_start_nm": 350,
            "grid_end_nm": 800,
            "step_nm": 0.1,
            "observed_intervals_nm": [[383, 468], [500, 800]],
            "max_gap_nm": 0.5,
            "wavelength_medium": "vacuum",
            "discard_first_shots": 5,
            "aggregation": "arithmetic_mean_of_retained_shots",
        },
        "selection": {
            "C_candidates": [0.01, 0.1, 1.0],
            "seed": SEED,
            "maximum_tuning_point_fpr": 0.05,
            "threshold_rule": (
                "unique known tuning scores plus nextafter(max,+inf); maximize physical recall "
                "subject to point FPR<=0.05; tie lower FPR then higher threshold"
            ),
            "model_rule": "maximum tuning macro balanced accuracy; tie smaller C; no refit on tune",
            "baseline": (
                "fixed NIST neutral NNLS with thresholds selected on identical real tuning set"
            ),
            "primary_comparison": (
                "adapted logistic minus threshold-adapted NNLS on the untouched final campaign"
            ),
            "bootstrap_repeats": 2000,
            "bootstrap_seed": SEED,
            "test_used_for_selection": False,
        },
        "limitations": [
            "Published assays lack per-cell uncertainty; guarded labels do not certify absence.",
            "LANL testbed profile only; not validated for another instrument or Mars measurements.",
            "No blank experiment, H/He validation, probability calibration or concentration fit.",
            "Split uses metadata and compositions; no intensity or model result selects it.",
        ],
    }
    validate_protocol(protocol)
    if destination.exists():
        existing = json.loads(destination.read_text())
        candidate = {**protocol, "locked_at": existing["locked_at"]}
        if existing != candidate:
            raise ValueError(
                "Existing protocol differs: create a new reviewed protocol, do not overwrite"
            )
        return existing
    write_json(destination, protocol)
    return protocol


def read_features(root: Path, records: list[dict], grid: np.ndarray, config: dict) -> tuple:
    features, labels, details = [], [], []
    for record in records:
        waves, mean, meta = read_chemcam_ccs(
            root / record["local_relative_path"],
            record["input_sha256"],
            discard_first_shots=config["discard_first_shots"],
        )
        features.append(
            interpolate_observed(
                waves, mean, grid, config["observed_intervals_nm"], max_gap_nm=config["max_gap_nm"]
            )
        )
        labels.append(
            [
                {"positive": 1, "negative": 0, "unknown": -1}[record["labels"][c]["state"]]
                for c in CLASSES
            ]
        )
        details.append({"measurement_id": record["measurement_id"], **meta})
    return np.asarray(features), np.asarray(labels), details


def run(root: Path, protocol: dict, *, replay: bool = False) -> Path:
    admission = validate_protocol(protocol)
    locked_hash = admission["protocol_hash"]
    output = (
        root / "reports/adaptation" / f"chemcam-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True)
    write_json(output / "protocol.json", protocol)
    write_json(output / "admission.json", admission)
    result = {
        "schema_version": 1,
        "status": "running",
        "run_id": output.name,
        "protocol_hash": locked_hash,
        "started_at": datetime.now(UTC).isoformat(),
        "test_used_for_selection": False,
        "implementation_sha256": {
            str(path.relative_to(root)): sha(path)
            for path in [
                *sorted((root / "ml/src/spectralab_ml").rglob("*.py")),
                Path(__file__).resolve(),
            ]
        },
    }
    write_json(output / "result.json", result)
    try:
        cfg, selection = protocol["preprocessing"], protocol["selection"]
        processor = SpectrumPreprocessor(cfg["grid_start_nm"], cfg["grid_end_nm"], cfg["step_nm"])
        grid = processor.model_grid
        mask = observation_mask(grid, cfg["observed_intervals_nm"])
        parts = {
            key: [
                r
                for r in protocol["development"]
                if r["measurement_id"] in protocol["development_split"][key + "_ids"]
            ]
            for key in ("train", "tune")
        }
        audits = [
            guard_development_fit(
                protocol,
                [r["measurement_id"] for r in parts[key]],
                operation,
                expected_protocol_hash=locked_hash,
            )
            for key, operation in (("train", "instrument_adaptation"), ("tune", "threshold"))
        ]
        train_X, train_y, train_meta = read_features(root, parts["train"], grid, cfg)
        tune_X, tune_y, tune_meta = read_features(root, parts["tune"], grid, cfg)
        train_groups = [r["physical_sample_id"] for r in parts["train"]]
        tune_groups = [r["physical_sample_id"] for r in parts["tune"]]
        reference_path = root / protocol["reference"]["path"]
        if sha(reference_path) != protocol["reference"]["sha256"]:
            raise ValueError("Frozen baseline reference changed")
        catalog = ReferenceCatalog.from_cache(reference_path)
        templates = normalize_observed(
            make_reference_templates(catalog, processor, tuple(CLASSES)), mask
        )
        baseline = BaselineNNLS(templates)
        baseline_thresholds, baseline_selection = select_operational_thresholds(
            baseline.scores(normalize_observed(tune_X, mask)),
            tune_y,
            tune_groups,
            maximum_fpr=selection["maximum_tuning_point_fpr"],
        )
        np.savez_compressed(
            output / "baseline.npz",
            templates=templates,
            mask=mask,
            grid_nm=grid,
            thresholds=baseline_thresholds,
        )
        with (output / "baseline.npz").open("rb") as stream:
            os.fsync(stream.fileno())
        candidates, models = [], []
        for C in selection["C_candidates"]:
            model = InstrumentAdapter().fit(
                train_X,
                train_y == 1,
                train_y >= 0,
                sample_ids=train_groups,
                grid_nm=grid,
                observed_intervals=cfg["observed_intervals_nm"],
                class_order=CLASSES,
                instrument_id="chemcam-lanl-testbed",
                C=C,
                seed=selection["seed"],
                provenance={
                    "estimand": protocol["estimand"],
                    "oxide_policy": protocol["decision_policy"],
                    "protocol_hash": locked_hash,
                    "intake_sha256": protocol["intake_sha256"],
                    "fit_audits": audits,
                    "preprocessing": cfg,
                },
            )
            thresholds, tuning = select_operational_thresholds(
                model.scores(tune_X),
                tune_y,
                tune_groups,
                maximum_fpr=selection["maximum_tuning_point_fpr"],
            )
            model.save(
                output / "candidates" / f"C-{C:g}",
                thresholds=dict(zip(CLASSES, thresholds.tolist(), strict=True)),
            )
            candidates.append({"C": C, "selection": tuning, "model_identity": model.identity})
            models.append((model, thresholds))
        winner = max(
            range(len(candidates)),
            key=lambda i: (
                candidates[i]["selection"]["macro_balanced_accuracy"],
                -candidates[i]["C"],
            ),
        )
        model, thresholds = models[winner]
        model.save(
            output / "profile", thresholds=dict(zip(CLASSES, thresholds.tolist(), strict=True))
        )
        sync_directory(output / "profile")
        model = InstrumentAdapter.load(
            output / "profile",
            expected_grid_nm=grid,
            expected_observed_intervals=cfg["observed_intervals_nm"],
            expected_class_order=tuple(CLASSES),
            expected_instrument_id="chemcam-lanl-testbed",
        )
        # This receipt is durable before any final test spectrum is parsed/scored.
        model_lock = {
            "locked_at": datetime.now(UTC).isoformat(),
            "protocol_hash": locked_hash,
            "baseline_selection": baseline_selection,
            "baseline_sha256": sha(output / "baseline.npz"),
            "candidates": candidates,
            "selected_candidate": winner,
            "C": candidates[winner]["C"],
            "profile_identity": model.identity,
            "fit_audits": audits,
            "training_reader": train_meta,
            "tuning_reader": tune_meta,
        }
        write_json(output / "model_lock.json", model_lock)
        print(
            f"Models and thresholds locked: {output.name}; C={candidates[winner]['C']}", flush=True
        )
        if validate_protocol(protocol)["protocol_hash"] != locked_hash:
            raise ValueError("Protocol changed after fitting")
        test_content = sorted(
            (record["measurement_id"], record["input_sha256"]) for record in protocol["test"]
        )
        test_hash = hashlib.sha256(json.dumps(test_content).encode()).hexdigest()
        registry = root / "reports/adaptation/test_usage"
        registry.mkdir(parents=True, exist_ok=True)
        receipt = consume_test(
            registry / f"{test_hash}.json",
            {
                "test_content_hash": test_hash,
                "protocol_hash": locked_hash,
                "profile_identity": model.identity,
                "baseline_sha256": model_lock["baseline_sha256"],
                "implementation_sha256": result["implementation_sha256"],
            },
            replay=replay,
        )
        result["test_usage_receipt"] = receipt
        write_json(output / "result.json", result)
        test_X, _, test_meta = read_features(root, protocol["test"], grid, cfg)
        baseline_scores = baseline.scores(normalize_observed(test_X, mask))
        adapted_scores = model.scores(test_X)
        baseline_detected, adapted_detected = (
            baseline_scores >= baseline_thresholds,
            adapted_scores >= thresholds,
        )
        comparison = compare_detections(
            protocol,
            baseline_detected,
            adapted_detected,
            measurement_ids=admission["test_ids"],
            expected_protocol_hash=locked_hash,
            adaptation_audits=audits,
            bootstrap_repeats=selection["bootstrap_repeats"],
            bootstrap_seed=selection["bootstrap_seed"],
        )
        np.savez_compressed(
            output / "test_predictions.npz",
            baseline_scores=baseline_scores,
            adapted_scores=adapted_scores,
            baseline_detections=baseline_detected,
            adapted_detections=adapted_detected,
        )
        write_json(
            output / "test_predictions.json",
            [
                {
                    "measurement_id": record["measurement_id"],
                    "physical_sample_id": record["physical_sample_id"],
                    "baseline_scores": dict(zip(CLASSES, baseline_scores[i].tolist(), strict=True)),
                    "adapted_scores": dict(zip(CLASSES, adapted_scores[i].tolist(), strict=True)),
                    "baseline_detected": dict(
                        zip(CLASSES, baseline_detected[i].tolist(), strict=True)
                    ),
                    "adapted_detected": dict(
                        zip(CLASSES, adapted_detected[i].tolist(), strict=True)
                    ),
                }
                for i, record in enumerate(protocol["test"])
            ],
        )
        result.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            model_lock=model_lock,
            comparison=comparison,
            test_reader=test_meta,
            test_predictions_sha256=sha(output / "test_predictions.npz"),
        )
        write_json(output / "result.json", result)
        print(output, flush=True)
        return output
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write_json(output / "result.json", result)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument(
        "--replay", action="store_true", help="Explicitly reproduce the same locked test"
    )
    args = parser.parse_args()
    root = repository_root()
    protocol = freeze(root, root / "data/reference/chemcam_adaptation_protocol.json")
    if args.freeze_only:
        print(json.dumps(validate_protocol(protocol), indent=2))
    else:
        run(root, protocol, replay=args.replay)


if __name__ == "__main__":
    main()
