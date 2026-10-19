from __future__ import annotations

from dataclasses import asdict, dataclass
from numbers import Real

import numpy as np


def validate_spectrum_arrays(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    *,
    max_points: int = 100_000,
) -> None:
    if wavelength_nm.ndim != 1 or intensity.ndim != 1:
        raise ValueError("Wavelength and intensity must be one-dimensional")
    if wavelength_nm.size != intensity.size:
        raise ValueError("Wavelength and intensity arrays must have equal length")
    if wavelength_nm.size < 2:
        raise ValueError("At least two points are required")
    if wavelength_nm.size > max_points:
        raise ValueError(f"Spectrum exceeds the {max_points} point limit")
    if not np.all(np.isfinite(wavelength_nm)) or not np.all(np.isfinite(intensity)):
        raise ValueError("Spectrum values must be finite")
    if np.any(np.diff(wavelength_nm) <= 0):
        raise ValueError("Wavelengths must be strictly increasing without duplicates")
    if np.max(np.abs(intensity)) == 0:
        raise ValueError("Zero-only spectra cannot be analysed")


@dataclass(frozen=True)
class SpectrumPreprocessor:
    wavelength_start_nm: float = 350.0
    wavelength_end_nm: float = 800.0
    model_step_nm: float = 0.05
    normalization: str = "max_abs"

    def __post_init__(self) -> None:
        values = (self.wavelength_start_nm, self.wavelength_end_nm, self.model_step_nm)
        if any(isinstance(v, bool) or not isinstance(v, Real) for v in values):
            raise ValueError("Preprocessing grid values must be finite numbers")
        try:
            start, end, step = (float(v) for v in values)
        except OverflowError as exc:
            raise ValueError("Preprocessing grid values exceed the finite numeric range") from exc
        if not np.isfinite([start, end, step]).all() or not 0 < start < end or step <= 0:
            raise ValueError("Preprocessing grid requires a positive ordered range and step")
        intervals = (end - start) / step
        if (
            not np.isfinite(intervals)
            or not 1 <= intervals <= 99_999
            or not np.isclose(intervals, round(intervals), rtol=0, atol=1e-8)
        ):
            raise ValueError(
                "Preprocessing grid needs 2..100000 points and an exactly dividing step"
            )
        if not isinstance(self.normalization, str) or self.normalization not in {"max_abs", "l2"}:
            raise ValueError("Unsupported preprocessing normalization")

    @property
    def model_grid(self) -> np.ndarray:
        count = round((self.wavelength_end_nm - self.wavelength_start_nm) / self.model_step_nm) + 1
        return self.wavelength_start_nm + np.arange(count) * self.model_step_nm

    def fit(self, spectra: list[tuple[np.ndarray, np.ndarray]]) -> SpectrumPreprocessor:
        for wavelength, intensity in spectra:
            self.transform(wavelength, intensity)
        return self

    def transform(self, wavelength_nm: np.ndarray, intensity: np.ndarray) -> np.ndarray:
        wavelength_nm = np.asarray(wavelength_nm, dtype=float)
        intensity = np.asarray(intensity, dtype=float)
        validate_spectrum_arrays(wavelength_nm, intensity)
        tolerance = 0.0
        if (
            wavelength_nm[0] > self.wavelength_start_nm + tolerance
            or wavelength_nm[-1] < self.wavelength_end_nm - tolerance
        ):
            raise ValueError(
                f"Spectrum must cover {self.wavelength_start_nm:g}–{self.wavelength_end_nm:g} nm"
            )
        vector = np.interp(self.model_grid, wavelength_nm, intensity)
        if self.normalization == "max_abs":
            scale = float(np.max(np.abs(vector)))
        elif self.normalization == "l2":
            scale = float(np.linalg.norm(vector))
        else:
            raise ValueError(f"Unsupported normalization: {self.normalization}")
        if scale == 0 or not np.isfinite(scale):
            raise ValueError("Spectrum has no finite non-zero scale")
        result = vector / scale
        if not np.all(np.isfinite(result)):
            raise ValueError("Preprocessed spectrum contains non-finite values")
        return result

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, float | str]) -> SpectrumPreprocessor:
        return cls(**value)  # type: ignore[arg-type]
