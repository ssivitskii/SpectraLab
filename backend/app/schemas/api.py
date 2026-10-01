from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class PerElementMetrics(BaseModel):
    support: int
    precision: float | None
    recall: float | None


class SubsetMetrics(BaseModel):
    count: int
    micro_f1: float | None
    exact_match_accuracy: float | None


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
    seed: int
    runs: list[ExperimentCondition]
    limitations: str
