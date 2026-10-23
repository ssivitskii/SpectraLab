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


class WorkerCurrentTask(BaseModel):
    kind: Literal["job", "batch"]
    task_id: str
    description: str | None = None
    started_at: str | None = None


class WorkerStatus(BaseModel):
    state: Literal["absent", "running", "stopped", "stale"]
    status: Literal["absent", "running", "stopped", "stale"] | None = None
    worker_id: str | None = None
    pid: int | None = None
    updated_at: str | None = None
    current_task: WorkerCurrentTask | None = None
    heartbeat_interval_seconds: float
    stale_threshold_seconds: float
    message: str


class JobQueueCounts(BaseModel):
    total: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    quota_pending: int
    remaining_pending: int


class BatchQueueCounts(BaseModel):
    total: int = 0
    draft: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    partial: int = 0
    failed: int = 0
    cancelled: int = 0


class QueueStatus(BaseModel):
    jobs: JobQueueCounts
    batches: BatchQueueCounts
    active_total: int = 0


class StorageCategory(BaseModel):
    record_count: int
    size_bytes: int
    quota_records: int | None = None
    remaining_records: int | None = None
    quota_bytes: int | None = None
    remaining_bytes: int | None = None


class DiskUsageStatus(BaseModel):
    total_bytes: int
    used_bytes: int
    free_bytes: int


class StorageStatus(BaseModel):
    categories: dict[str, StorageCategory]
    total_used_bytes: int = 0
    quota_headroom_bytes: int | None = None
    disk: DiskUsageStatus


class BackupVerificationRecord(BaseModel):
    completed_at: str
    archive_name: str
    archive_sha256: str
    archive_size_bytes: int
    records_count: int
    verification_passed: bool
    details: str | None = None


class RestoreVerificationRecord(BaseModel):
    completed_at: str
    archive_name: str
    archive_sha256: str
    restored_records_count: int
    verification_passed: bool
    details: str | None = None


class BackupStatus(BaseModel):
    last_archive_verified: BackupVerificationRecord | None = None
    last_restore_verified: RestoreVerificationRecord | None = None


class SystemStatusResponse(BaseModel):
    worker: WorkerStatus
    queues: QueueStatus
    storage: StorageStatus
    backup: BackupStatus


class ScaleRequirements(BaseModel):
    units: Literal["nm"] = "nm"
    medium: Literal["vacuum"] = "vacuum"
    conversion_supported: list[str] = ["air"]
    conversion_method: str = "Peck-Reeder 1972 standard air"


class CoverageRequirements(BaseModel):
    bands_nm: list[list[float]]
    max_gap_nm: float
    max_sampling_step_nm: float
    max_points: int


class InstrumentProfileInfo(BaseModel):
    profile_id: str
    artifact_type: str
    schema_version: int
    identity: str
    sha256: str
    instrument_id: str
    supported_elements: list[str]
    unknown_elements: list[str]
    thresholds: dict[str, float]
    scale_requirements: ScaleRequirements
    coverage_requirements: CoverageRequirements
    ready: bool
    unavailable_reason: str | None = None
    qualification: str
    scope: str
    final_test_status: str
    score_kind: str


class MeasuredPredictionElement(BaseModel):
    element: str
    status: Literal["evaluated", "unknown"]
    score: float | None = None
    threshold: float | None = None
    detected: bool | None = None
    accepted: bool
    line_snr: float | None = None
    reason: str | None = None


class MeasuredPredictionPoint(StrictModel):
    wavelength_nm: float = Field(..., ge=100.0, le=2000.0)
    intensity: float = Field(...)


class MeasuredPredictionResponse(BaseModel):
    schema_version: int = 3
    prediction_id: str
    profile_id: str
    profile_identity: str
    profile_sha256: str
    profile_artifact_type: str
    scope: str
    qualification: str
    final_test_status: str
    measurement_id: str
    instrument_id: str
    score_kind: str
    calibrated: bool
    calibration_scope: str
    created_at: str
    predictions: list[MeasuredPredictionElement]
    detected_elements: list[str]
    wavelength_conversion: dict[str, Any] | None = None
    input_axis: dict[str, Any]
    model_axis: dict[str, Any]
    bands_nm: list[list[float]]
    input_provenance: dict[str, Any]
    physical_sample_id: str | None = None
    family_id: str | None = None
    session_id: str | None = None
    measurement_metadata: dict[str, Any] | None = None


class AxisMetadata(StrictModel):
    units: Literal["nm"] = Field(..., description="Wavelength axis units, must be nm")
    medium: Literal["air", "vacuum"] = Field(..., description="Measurement medium")
    source: str = Field(..., min_length=1, max_length=200, description="Axis calibration source")
    source_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 hash of axis calibration source",
    )


class MeasurementMetadataPayload(StrictModel):
    schema_version: Literal[1] = Field(default=1, description="Schema version, must be 1")
    measurement_id: str | None = Field(default=None, min_length=1, max_length=128)
    instrument_id: str | None = Field(default=None, min_length=1, max_length=128)
    physical_sample_id: str | None = Field(default=None, min_length=1, max_length=128)
    family_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    axis: AxisMetadata = Field(..., description="Explicit axis convention and provenance")
    input_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class MeasuredPredictionRequestJson(StrictModel):
    profile_id: str | None = Field(default=None, max_length=128)
    instrument_id: str | None = Field(default=None, max_length=128)
    medium: Literal["air", "vacuum"] | None = None
    units: Literal["nm"] | None = Field(default=None, max_length=16)
    measurement_id: str | None = Field(default=None, max_length=128)
    physical_sample_id: str | None = Field(default=None, max_length=128)
    family_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    csv_content: str | None = Field(default=None, max_length=16_000_000)
    wavelength_nm: list[float] | None = None
    intensity: list[float] | None = None
    points: list[MeasuredPredictionPoint] | None = None
    metadata: MeasurementMetadataPayload | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> MeasuredPredictionRequestJson:
        has_csv = self.csv_content is not None
        has_points = self.points is not None
        has_arrays = self.wavelength_nm is not None or self.intensity is not None

        representations = sum([bool(has_csv), bool(has_points), bool(has_arrays)])
        if representations != 1:
            raise ValueError(
                "Exactly one spectrum representation is required: 'csv_content', 'points', "
                "or ('wavelength_nm' and 'intensity')"
            )
        if has_arrays:
            if self.wavelength_nm is None or self.intensity is None:
                raise ValueError("Both 'wavelength_nm' and 'intensity' arrays are required")
            if len(self.wavelength_nm) != len(self.intensity):
                raise ValueError("'wavelength_nm' and 'intensity' must have matching lengths")
            if len(self.wavelength_nm) == 0:
                raise ValueError("Arrays must not be empty")
            if any(not math.isfinite(w) for w in self.wavelength_nm) or any(
                not math.isfinite(i) for i in self.intensity
            ):
                raise ValueError("Array values must be finite numbers")
        if has_points:
            if len(self.points) == 0:
                raise ValueError("points list must not be empty")
            if any(not math.isfinite(p.intensity) for p in self.points):
                raise ValueError("Point intensity values must be finite numbers")
        if has_csv and not self.csv_content.strip():
            raise ValueError("csv_content must not be empty")
        return self


class MeasuredPredictionFormRequest(StrictModel):
    file: bytes = Field(..., description="CSV file content (wavelength_nm,intensity)")
    profile_id: str | None = Field(default=None, max_length=128)
    instrument_id: str | None = Field(default=None, max_length=128)
    medium: Literal["air", "vacuum"] | None = None
    units: Literal["nm"] | None = Field(default=None, max_length=16)
    measurement_id: str | None = Field(default=None, max_length=128)
    physical_sample_id: str | None = Field(default=None, max_length=128)
    family_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    metadata: str | None = Field(default=None, description="Optional JSON-encoded metadata string")
