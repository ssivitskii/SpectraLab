from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeasurementMetadata(StrictModel):
    sample_id: str | None = Field(default=None, max_length=200)
    instrument_id: str | None = Field(default=None, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)
    calibration: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)


class GenerateSpectrumRequest(StrictModel):
    elements: list[str] = Field(min_length=1, max_length=3)
    component_weights: list[float] = Field(min_length=1, max_length=3)
    instrumental_fwhm_nm: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    sampling_step_nm: float = Field(default=0.05, gt=0, allow_inf_nan=False)
    snr_db: float | None = Field(default=20, allow_inf_nan=False)
    background_level: float = Field(default=0, allow_inf_nan=False)
    background_slope: float = Field(default=0, allow_inf_nan=False)
    calibration_shift_nm: float = Field(default=0, allow_inf_nan=False)
    amplitude_variation: float = Field(default=0.08, ge=0, allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    profile: Literal["gaussian", "voigt"] = "gaussian"
    lorentzian_fwhm_nm: float = Field(default=0, ge=0, allow_inf_nan=False)
    noise_model: Literal["gaussian", "poisson_read"] = "gaussian"
    photon_scale: float = Field(default=1000, gt=0, allow_inf_nan=False)
    read_noise_std: float = Field(default=0, ge=0, allow_inf_nan=False)
    response_slope: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    saturation_level: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    ion_stage: int | None = Field(default=None, ge=1, le=5)
    measurement_metadata: MeasurementMetadata = Field(default_factory=MeasurementMetadata)

    @model_validator(mode="after")
    def validate_weights(self) -> GenerateSpectrumRequest:
        if len(self.component_weights) != len(self.elements):
            raise ValueError("component_weights must match elements")
        if len(set(self.elements)) != len(self.elements):
            raise ValueError("elements must be unique")
        if any(not math.isfinite(value) or value <= 0 for value in self.component_weights):
            raise ValueError("component_weights must be positive and finite")
        return self


class SpectrumResponse(BaseModel):
    spectrum_id: str
    wavelength_nm: list[float]
    intensity: list[float]
    clean_signal: list[float] | None = None
    true_labels: dict[str, int] | None = None
    parameters: dict[str, Any]
    base_sample_id: str | None = None
    reference_source: str
    origin: Literal["generated", "uploaded"]
    schema_version: int = 1
    created_at: str = ""
    measurement_metadata: MeasurementMetadata = Field(default_factory=MeasurementMetadata)
    computed: dict[str, Any] = Field(default_factory=dict)
    reference_metadata: dict[str, Any] = Field(default_factory=dict)


class PredictByIdRequest(StrictModel):
    input_type: Literal["spectrum_id"]
    spectrum_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    model_id: str = Field(default="builtin-nnls", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class PredictArraysRequest(StrictModel):
    input_type: Literal["arrays"]
    wavelength_nm: list[float] = Field(min_length=2, max_length=100_000)
    intensity: list[float] = Field(min_length=2, max_length=100_000)
    units: Literal["nm"]
    wavelength_medium: Literal["vacuum"]
    model_id: str = Field(default="builtin-nnls", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


PredictRequest = Annotated[
    PredictByIdRequest | PredictArraysRequest, Field(discriminator="input_type")
]


class ElementScore(BaseModel):
    element: str
    value: float


class MatchedReferenceLine(BaseModel):
    element: str
    wavelength_nm: float
    relative_intensity: float | None
    kind: str
    observed_peak_nm: float
    delta_nm: float


class PredictionResponse(BaseModel):
    prediction_id: str | None = None
    spectrum_id: str | None = None
    created_at: str | None = None
    schema_version: int = 1
    model_id: str
    model_version: str
    model_origin: str
    reference_source: str
    scores: list[ElementScore]
    score_kind: str
    calibrated: bool
    thresholds: dict[str, float]
    detected_elements: list[str]
    warnings: list[str]
    processing_time_ms: float
    matched_reference_lines: list[MatchedReferenceLine]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ModelInfo(BaseModel):
    model_id: str
    version: str | None
    model_type: str
    ready: bool
    score_kind: str | None
    calibrated: bool
    data_source: str
    applicability: str
    unavailable_reason: str | None = None
    diagnostic_code: str | None = None


class ElementsInfo(BaseModel):
    elements: list[str]
    ion_stage: int
    reference: dict[str, Any]
    line_counts: dict[str, int]


class ExperimentSummary(BaseModel):
    run_id: str
    status: str
    created_at: str
    reference_source: str
    protocol: str
    demo: bool
    artifact_type: str = "experiment"
    error: str | dict[str, Any] | None = None


class PerElementMetrics(BaseModel):
    support: int
    precision: float | None
    recall: float | None


class SubsetMetrics(BaseModel):
    count: int
    micro_f1: float | None
    exact_match_accuracy: float | None


class InferenceTiming(BaseModel):
    warmups: int = Field(ge=1)
    repeats: int = Field(ge=2)
    batch_size: int = Field(ge=1)
    unit: Literal["ms_per_spectrum"]
    median: float = Field(ge=0, allow_inf_nan=False)
    p95: float = Field(ge=0, allow_inf_nan=False)
    observations: list[float]


class ExperimentMetrics(BaseModel):
    micro_f1: float
    macro_f1: float
    exact_match_accuracy: float
    hamming_loss: float
    per_element: dict[str, PerElementMetrics]
    single: SubsetMetrics
    mixture: SubsetMetrics
    undefined_metric_policy: str
    inference_ms_per_spectrum: float | None = None
    timing_method: str | None = None
    timing: InferenceTiming | None = None


class ExperimentCondition(BaseModel):
    model_id: str
    factor: str
    value: float | None
    requested_value: float | str | None
    test_base_sample_ids: list[str]
    metrics: ExperimentMetrics


class ExperimentDetail(ExperimentSummary):
    # Keep provenance fields even for running/failed runs with incomplete artifacts.
    model_config = ConfigDict(extra="allow")
    configuration: dict[str, Any]
    seed: int | None = None
    runs: list[ExperimentCondition] = Field(default_factory=list)
    limitations: str = ""
    schema_version: int = 1
    reference_metadata: dict[str, Any] = Field(default_factory=dict)
    config_hash: str | None = None
    source_hash: str | None = None
    expected_seeds: list[int] = Field(default_factory=list)
    successful_seeds: list[int] = Field(default_factory=list)
    seed_runs: list[SeriesSeedRun] = Field(default_factory=list)
    aggregates: list[SeriesAggregate] = Field(default_factory=list)
    incomplete_runs: list[SeriesIncompleteRun] = Field(default_factory=list)
    failed_runs: list[SeriesFailedRun] = Field(default_factory=list)


class SeriesSeedRun(BaseModel):
    seed: int
    run_id: str | None = None
    status: str
    config_hash: str | None = None
    source_hash: str | None = None
    error: str | dict[str, Any] | None = None


class SeriesIncompleteRun(SeriesSeedRun):
    status: str = "incomplete"


class SeriesFailedRun(SeriesSeedRun):
    status: str = "failed"


class AggregateStatistic(BaseModel):
    mean: float | None
    std: float | None
    count: int


class SeriesAggregate(BaseModel):
    model_id: str
    factor: str
    value: float | str | None
    metrics: dict[str, AggregateStatistic | dict[str, AggregateStatistic]]


class ExperimentPage(BaseModel):
    items: list[ExperimentSummary]
    next_cursor: str | None


class SpectrumSummary(BaseModel):
    spectrum_id: str
    created_at: str
    origin: Literal["generated", "uploaded"]
    reference_source: str
    measurement_metadata: MeasurementMetadata
    computed: dict[str, Any]


class SpectrumPage(BaseModel):
    items: list[SpectrumSummary]
    next_cursor: str | None


class SpectrumCapabilities(BaseModel):
    wavelength_start_nm: float
    wavelength_end_nm: float
    model_step_nm: float
    units: Literal["nm"] = "nm"
    wavelength_medium: Literal["vacuum"] = "vacuum"
    max_points: int
    max_upload_bytes: int
    csv_columns: list[str] = ["wavelength_nm", "intensity"]


class GenerationCapabilities(BaseModel):
    max_components: int = 3
    seed_min: int = 0
    seed_max: int = 2_147_483_647
    min_sampling_step_nm: float
    defaults: dict[str, Any]
    profiles: list[str] = ["gaussian", "voigt"]
    noise_models: list[str] = ["gaussian", "poisson_read"]
    ion_stages: list[int] = [1]
    limits: dict[str, NumericLimit] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)


class NumericLimit(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = False


class ServiceCapabilities(BaseModel):
    api_version: str = "1.1"
    schema_versions: dict[str, int]
    spectrum: SpectrumCapabilities
    generation: GenerationCapabilities
    reference: dict[str, Any]
    elements: list[str]
    storage: dict[str, int]
    jobs: dict[str, Any]
    batches: dict[str, Any] = Field(default_factory=dict)
    deployment_mode: Literal["local"] = "local"


class JobRequest(StrictModel):
    seeds: list[int] = Field(min_length=1, max_length=10)
    config_name: Literal["demo", "default"] = "demo"
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")

    @model_validator(mode="after")
    def validate_seeds(self) -> JobRequest:
        if len(set(self.seeds)) != len(self.seeds) or any(
            seed < 0 or seed > 2_147_483_647 for seed in self.seeds
        ):
            raise ValueError("seeds must be unique integers in [0, 2147483647]")
        return self


class JobProgress(BaseModel):
    completed: int = 0
    total: int


class JobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: str
    updated_at: str
    seeds: list[int]
    config_name: str
    progress: JobProgress
    run_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancellation_requested: bool = False
    idempotency_key: str


class BatchFileDescriptor(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0, le=1_000_000_000_000)


class BatchRequest(StrictModel):
    name: str = Field(default="CSV folder analysis", min_length=1, max_length=200)
    model_ids: list[str] = Field(min_length=1, max_length=5)
    expected_files: list[BatchFileDescriptor] = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    measurement_metadata: MeasurementMetadata = Field(default_factory=MeasurementMetadata)

    @model_validator(mode="after")
    def unique_models_and_paths(self) -> BatchRequest:
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError("Batch model IDs must be unique")
        if len({file.relative_path for file in self.expected_files}) != len(self.expected_files):
            raise ValueError("Batch relative paths must be unique")
        return self


class BatchRejection(BatchFileDescriptor):
    error_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    error_message: str = Field(min_length=1, max_length=1000)


class BatchFile(BatchFileDescriptor):
    file_id: str
    status: Literal["pending_upload", "ready", "invalid"]
    validation_origin: Literal["client", "server"] | None = None
    point_count: int | None = None
    spectrum_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    measurement_metadata: MeasurementMetadata


class BatchModelSnapshot(BaseModel):
    model_id: str
    identity: str
    model_version: str
    model_sha256: str | None = None
    manifest_sha256: str | None = None
    reference_source: str
    reference_sha256: str
    preprocessing: dict[str, Any]
    class_order: list[str]
    ion_stage: int
    score_kind: str
    calibrated: bool


class BatchResult(BaseModel):
    file_id: str
    model_id: str
    model_identity: str
    status: Literal["pending", "running", "completed", "failed"]
    prediction_id: str | None = None
    prediction: PredictionResponse | None = None
    cache_hit: bool = False
    error_code: str | None = None
    error_message: str | None = None


class BatchProgress(BaseModel):
    completed: int
    failed: int
    total: int


class BatchResponse(BaseModel):
    schema_version: int = 1
    batch_id: str
    name: str
    status: Literal["draft", "queued", "running", "completed", "partial", "failed", "cancelled"]
    created_at: str
    updated_at: str
    model_ids: list[str]
    model_identities: dict[str, str]
    model_snapshots: dict[str, BatchModelSnapshot]
    files: list[BatchFile]
    results: list[BatchResult]
    progress: BatchProgress
    cancellation_requested: bool = False
    error_code: str | None = None
    error_message: str | None = None
    idempotency_key: str
