from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPECTRALAB_", env_file=".env", extra="ignore")

    project_root: Path = Path(__file__).resolve().parents[3]
    data_dir: Path | None = None
    artifacts_dir: Path | None = None
    reports_dir: Path | None = None
    cors_origins: list[str] = ["http://localhost:5173"]
    max_upload_bytes: int = 5_000_000
    max_request_bytes: int = 6_000_000
    max_stored_spectra: int = 500
    max_storage_bytes: int = 500_000_000
    max_spectrum_points: int = 100_000
    reference_cache: Path | None = None
    reference_sha256: str | None = None
    max_stored_predictions: int = Field(default=1000, ge=1)
    max_prediction_storage_bytes: int = Field(default=100_000_000, ge=1000)
    max_pending_jobs: int = Field(default=10, ge=1, le=100)
    max_job_records: int = Field(default=1000, ge=1)
    job_timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    worker_memory_mb: int = Field(default=4096, ge=256, le=65536)
    max_batch_files: int = Field(default=200, ge=1, le=200)
    max_batch_records: int = Field(default=100, ge=1, le=10000)
    max_batch_upload_bytes: int = Field(default=100_000_000, ge=1000)
    max_batch_input_storage_bytes: int = Field(default=500_000_000, ge=1000)
    max_batch_cache_bytes: int = Field(default=100_000_000, ge=1000)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    worker_heartbeat_stale_seconds: float = Field(default=20.0, ge=1.0, le=300.0)
    max_stored_measured_predictions: int = Field(default=1000, ge=1)
    max_measured_prediction_storage_bytes: int = Field(default=100_000_000, ge=1000)
    instrument_profiles_path: Path | None = None

    def resolved_data_dir(self) -> Path:
        return (self.data_dir or self.project_root / "data").resolve()

    def resolved_artifacts_dir(self) -> Path:
        return (self.artifacts_dir or self.project_root / "artifacts").resolve()

    def resolved_reports_dir(self) -> Path:
        return (self.reports_dir or self.project_root / "reports").resolve()


settings = Settings()
