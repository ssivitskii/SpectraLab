from __future__ import annotations

from pathlib import Path

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

    def resolved_data_dir(self) -> Path:
        return (self.data_dir or self.project_root / "data").resolve()

    def resolved_artifacts_dir(self) -> Path:
        return (self.artifacts_dir or self.project_root / "artifacts").resolve()

    def resolved_reports_dir(self) -> Path:
        return (self.reports_dir or self.project_root / "reports").resolve()


settings = Settings()
