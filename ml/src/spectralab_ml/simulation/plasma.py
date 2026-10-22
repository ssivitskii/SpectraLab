from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.signal import fftconvolve

from spectralab_ml.data.plasma_reference import (
    ATOMIC_MASS_U,
    BOLTZMANN_EV_PER_K,
    PlasmaReference,
)

PLANCK = 6.62607015e-34
LIGHT_SPEED = 2.99792458e8
BOLTZMANN = 1.380649e-23
ATOMIC_MASS_KG = 1.66053906660e-27
SAHA_CM3 = 2.4146830e15
CONTINUUM_SI = 1.632e-43
DEFAULT_TARGETS = ("H", "He", "Na", "Mg", "Ca", "Fe")
DEFAULT_INTERFERENTS = ("O", "Si", "Al", "K", "Ti", "Mn", "C")


@dataclass(frozen=True)
class PlasmaZone:
    temperature_k: float
    electron_density_cm3: float
    weight: float = 1.0

    def validate(self) -> None:
        if not 3000 <= self.temperature_k <= 40000:
            raise ValueError("Zone temperature must be within [3000, 40000] K")
        if not 1e13 <= self.electron_density_cm3 <= 1e19:
            raise ValueError("Electron density must be within [1e13, 1e19] cm^-3")
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError("Zone weight must be positive")


@dataclass(frozen=True)
class PlasmaConditions:
    zones: tuple[PlasmaZone, ...]
    heavy_density_cm3: float = 1e17
    path_length_cm: float = 0.1
    stark_fwhm_nm: float = 0.05
    stark_spread: float = 0.0
    hydrogen_stark_fwhm_nm: float = 1.0
    continuum_factor: float = 1.0
    min_intrinsic_fwhm_nm: float = 0.01

    def validate(self) -> None:
        if not self.zones:
            raise ValueError("At least one plasma zone is required")
        for zone in self.zones:
            zone.validate()
        checks = (
            ("heavy_density_cm3", 1e13, 1e20),
            ("path_length_cm", 1e-4, 10),
            ("stark_fwhm_nm", 0, 5),
            ("stark_spread", 0, 2),
            ("hydrogen_stark_fwhm_nm", 0, 20),
            ("continuum_factor", 0, 1e3),
            ("min_intrinsic_fwhm_nm", 1e-4, 1),
        )
        for name, low, high in checks:
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be within [{low}, {high}]")


def debye_lowering_ev(temperature_k: float, electron_density_cm3: float, charge: int) -> float:
    return 2.9e-8 * charge * math.sqrt(electron_density_cm3 / temperature_k)


def mass_to_number_fractions(mass_fractions: dict[str, float]) -> dict[str, float]:
    if not mass_fractions or any(not math.isfinite(v) or v < 0 for v in mass_fractions.values()):
        raise ValueError("Mass fractions must be finite and nonnegative")
    moles = {el: value / ATOMIC_MASS_U[el] for el, value in mass_fractions.items() if value > 0}
    total = sum(moles.values())
    if total <= 0:
        raise ValueError("Composition is empty")
    return {el: value / total for el, value in moles.items()}


class PlasmaModel:
    def __init__(
        self,
        reference: PlasmaReference,
        wavelength_start_nm: float,
        wavelength_end_nm: float,
        *,
        fine_step_nm: float = 0.005,
        margin_nm: float = 5.0,
    ):
        if not 0 < fine_step_nm <= 0.05:
            raise ValueError("Fine radiative-transfer step must be within (0, 0.05] nm")
        if wavelength_start_nm >= wavelength_end_nm:
            raise ValueError("Invalid wavelength range")
        self.reference = reference
        self.fine_step_nm = fine_step_nm
        start, end = wavelength_start_nm - margin_nm, wavelength_end_nm + margin_nm
        count = round((end - start) / fine_step_nm) + 1
        self.grid_nm = start + np.arange(count) * fine_step_nm
        selected = (reference.wavelength_nm >= start) & (reference.wavelength_nm <= end)
        self._lines = {
            name: getattr(reference, name)[selected]
            for name in (
                "element",
                "ion_stage",
                "wavelength_nm",
                "aki",
                "ei_ev",
                "ek_ev",
                "gi",
                "gk",
            )
        }

    def species_densities(
        self, number_fractions: dict[str, float], zone: PlasmaZone, heavy_density_cm3: float
    ) -> dict[tuple[str, int], tuple[float, float]]:
        reference, temperature = self.reference, zone.temperature_k
        kt = BOLTZMANN_EV_PER_K * temperature
        lowering = debye_lowering_ev(temperature, zone.electron_density_cm3, 1)
        result = {}
        for element, fraction in number_fractions.items():
            n_element = fraction * heavy_density_cm3
            u1 = reference.partition_function(element, 1, temperature, lowering_ev=lowering)
            u2 = reference.partition_function(element, 2, temperature, lowering_ev=2 * lowering)
            chi = reference.ionization_energy_ev(element, 1) - lowering
            ratio = (
                2 * SAHA_CM3 * temperature**1.5 * (u2 / u1) * math.exp(-chi / kt)
            ) / zone.electron_density_cm3
            result[(element, 1)] = (n_element / (1 + ratio), u1)
            result[(element, 2)] = (n_element * ratio / (1 + ratio), u2)
        return result

    def zone_radiance(
        self,
        number_fractions: dict[str, float],
        zone: PlasmaZone,
        conditions: PlasmaConditions,
        rng: np.random.Generator | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        temperature, density = zone.temperature_k, zone.electron_density_cm3
        kt = BOLTZMANN_EV_PER_K * temperature
        populations = self.species_densities(number_fractions, zone, conditions.heavy_density_cm3)
        lines = self._lines
        lowering = debye_lowering_ev(temperature, density, 1)
        n_stage = np.zeros(len(lines["aki"]))
        u_stage = np.ones(len(lines["aki"]))
        keep = np.zeros(len(lines["aki"]), dtype=bool)
        for (element, stage), (n_value, u_value) in populations.items():
            if f"{element}_{stage}" not in self.reference.species:
                continue
            match = (lines["element"] == element) & (lines["ion_stage"] == stage)
            limit = self.reference.ionization_energy_ev(element, stage) - stage * lowering
            match &= lines["ek_ev"] < limit
            n_stage[match], u_stage[match] = n_value, u_value
            keep |= match
        tau = np.zeros_like(self.grid_nm)
        if np.any(keep):
            wavelength = lines["wavelength_nm"][keep]
            lam = wavelength * 1e-9
            lower = (
                n_stage[keep]
                * 1e6
                * lines["gi"][keep]
                * np.exp(-lines["ei_ev"][keep] / kt)
                / u_stage[keep]
            )
            stimulated = -np.expm1(-PLANCK * LIGHT_SPEED / (lam * BOLTZMANN * temperature))
            integrated = (
                lam**4
                / (8 * math.pi * LIGHT_SPEED)
                * lines["aki"][keep]
                * lines["gk"][keep]
                / lines["gi"][keep]
                * lower
                * stimulated
            )
            tau_area = integrated * conditions.path_length_cm * 1e-2 * 1e9
            elements = lines["element"][keep]
            masses = np.asarray([ATOMIC_MASS_U[e] for e in elements]) * ATOMIC_MASS_KG
            doppler_fwhm = (
                wavelength
                * math.sqrt(8 * math.log(2) * BOLTZMANN * temperature)
                / np.sqrt(masses)
                / LIGHT_SPEED
            )
            stark = np.full(len(wavelength), conditions.stark_fwhm_nm * density / 1e17)
            if conditions.stark_spread and rng is not None:
                stark *= rng.lognormal(0.0, conditions.stark_spread, size=len(stark))
            hydrogen = elements == "H"
            stark[hydrogen] = conditions.hydrogen_stark_fwhm_nm * (density / 1e17) ** (2 / 3)
            tau = self._optical_depth(wavelength, tau_area, stark, doppler_fwhm, conditions)
        lam_grid = self.grid_nm * 1e-9
        x = PLANCK * LIGHT_SPEED / (lam_grid * BOLTZMANN * temperature)
        planck_photons = 2 * LIGHT_SPEED / lam_grid**4 / np.expm1(x)
        radiance = planck_photons * -np.expm1(-tau)
        ions = sum(n for (_, stage), (n, _) in populations.items() if stage == 2) * 1e6
        continuum_energy = (
            CONTINUUM_SI * density * 1e6 * ions / math.sqrt(temperature) / lam_grid**2
        )
        radiance = radiance + (
            conditions.continuum_factor
            * continuum_energy
            * lam_grid
            / (PLANCK * LIGHT_SPEED)
            * conditions.path_length_cm
            * 1e-2
        )
        return radiance * 1e-9, tau

    def _optical_depth(
        self,
        wavelength: np.ndarray,
        tau_area: np.ndarray,
        lorentz_fwhm: np.ndarray,
        gauss_fwhm: np.ndarray,
        conditions: PlasmaConditions,
    ) -> np.ndarray:
        step = self.fine_step_nm
        lorentz = np.maximum(lorentz_fwhm, 0)
        total = 0.5346 * lorentz + np.sqrt(0.2166 * lorentz**2 + gauss_fwhm**2)
        total = np.maximum(total, conditions.min_intrinsic_fwhm_nm)
        ratio = np.clip(lorentz / total, 0, 1)
        eta = np.clip(1.36603 * ratio - 0.47719 * ratio**2 + 0.11116 * ratio**3, 0, 1)
        width_class = np.floor(np.log(total / conditions.min_intrinsic_fwhm_nm) / 0.12).astype(int)
        eta_class = np.minimum((eta * 3).astype(int), 2)
        position = (wavelength - self.grid_nm[0]) / step
        index = np.floor(position).astype(int)
        frac = position - index
        valid = (index >= 0) & (index < len(self.grid_nm) - 1) & (tau_area > 0)
        tau = np.zeros_like(self.grid_nm)
        keys = np.stack([width_class, eta_class], axis=1)[valid]
        if not len(keys):
            return tau
        groups, inverse = np.unique(keys, axis=0, return_inverse=True)
        valid_index = np.flatnonzero(valid)
        for group in range(len(groups)):
            members = valid_index[inverse.ravel() == group]
            deposit = np.zeros_like(self.grid_nm)
            np.add.at(deposit, index[members], tau_area[members] * (1 - frac[members]))
            np.add.at(deposit, index[members] + 1, tau_area[members] * frac[members])
            fwhm = float(np.mean(total[members]))
            mixing = float(np.mean(eta[members]))
            half = int(min(max(40 * fwhm, 6 * fwhm), 6.0) / step)
            offsets = np.arange(-half, half + 1) * step
            lorentzian = (fwhm / 2) / math.pi / (offsets**2 + (fwhm / 2) ** 2)
            gaussian = (
                math.sqrt(4 * math.log(2) / math.pi)
                / fwhm
                * np.exp(-4 * math.log(2) * offsets**2 / fwhm**2)
            )
            kernel = mixing * lorentzian + (1 - mixing) * gaussian
            kernel /= kernel.sum() * step
            tau += fftconvolve(deposit, kernel, mode="same")
        return np.maximum(tau, 0)

    def radiance(
        self,
        number_fractions: dict[str, float],
        conditions: PlasmaConditions,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        conditions.validate()
        unknown = set(number_fractions) - set(self.reference.elements)
        if unknown:
            raise ValueError(f"No atomic data for: {', '.join(sorted(unknown))}")
        total = np.zeros_like(self.grid_nm)
        for zone in conditions.zones:
            radiance, _ = self.zone_radiance(number_fractions, zone, conditions, rng)
            total += zone.weight * radiance
        return total


@dataclass(frozen=True)
class InstrumentBand:
    start_nm: float
    end_nm: float
    fwhm_nm: float


def apply_instrument(
    fine_grid_nm: np.ndarray,
    radiance: np.ndarray,
    detector_grid_nm: np.ndarray,
    bands: tuple[InstrumentBand, ...],
    *,
    shift_nm: float = 0.0,
    stretch: float = 0.0,
) -> np.ndarray:
    step = float(fine_grid_nm[1] - fine_grid_nm[0])
    pixel = np.diff(detector_grid_nm)
    if np.any(pixel <= 0):
        raise ValueError("Detector grid must increase")
    edges = np.r_[
        detector_grid_nm[0] - pixel[0] / 2,
        (detector_grid_nm[1:] + detector_grid_nm[:-1]) / 2,
        detector_grid_nm[-1] + pixel[-1] / 2,
    ]
    true_edges = edges * (1 + stretch) + shift_nm
    if true_edges[0] < fine_grid_nm[0] or true_edges[-1] > fine_grid_nm[-1]:
        raise ValueError("Fine radiance grid does not cover the detector")
    result = np.zeros(len(detector_grid_nm))
    covered = np.zeros(len(detector_grid_nm), dtype=bool)
    for band in bands:
        sigma = band.fwhm_nm / (2 * math.sqrt(2 * math.log(2)))
        half = math.ceil(5 * sigma / step)
        offsets = np.arange(-half, half + 1) * step
        kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
        smoothed = fftconvolve(radiance, kernel / kernel.sum(), mode="same")
        cumulative = np.r_[0.0, np.cumsum(smoothed) * step]
        cumulative_axis = np.r_[fine_grid_nm[0] - step / 2, fine_grid_nm + step / 2]
        integral = np.interp(true_edges, cumulative_axis, cumulative)
        averaged = np.diff(integral) / np.diff(true_edges)
        inside = (detector_grid_nm >= band.start_nm) & (detector_grid_nm <= band.end_nm)
        result[inside] = averaged[inside]
        covered |= inside
    if not np.all(covered):
        result[~covered] = 0.0
    return result


@dataclass(frozen=True)
class RandomizationRanges:
    hot_temperature_k: tuple[float, float] = (9000.0, 14000.0)
    hot_density_cm3: tuple[float, float] = (3e16, 2e17)
    cool_temperature_k: tuple[float, float] = (5500.0, 9000.0)
    cool_density_cm3: tuple[float, float] = (5e15, 5e16)
    cool_weight: tuple[float, float] = (0.3, 1.5)
    heavy_density_cm3: tuple[float, float] = (3e16, 3e17)
    path_length_cm: tuple[float, float] = (0.03, 0.2)
    stark_fwhm_nm: tuple[float, float] = (0.02, 0.15)
    stark_spread: float = 0.5
    continuum_factor: tuple[float, float] = (0.1, 3.0)
    presence_probability: float = 0.55
    target_mass_fraction: tuple[float, float] = (2e-4, 0.3)
    hydrogen_mass_fraction: tuple[float, float] = (1e-4, 0.02)
    helium_mass_fraction: tuple[float, float] = (1e-3, 0.05)
    interferents: dict[str, tuple[float, float, float]] = field(
        default_factory=lambda: {
            "Si": (0.9, 0.02, 0.35),
            "Al": (0.85, 0.005, 0.15),
            "K": (0.7, 1e-4, 0.05),
            "Ti": (0.7, 1e-4, 0.03),
            "Mn": (0.7, 5e-5, 0.01),
            "C": (0.8, 1e-3, 0.05),
        }
    )
    oxygen_per_cation_mass: float = 0.8
    shift_nm_std: float = 0.05
    stretch_std: float = 1e-4
    response_coefficient_std: float = 0.2
    baseline_level: tuple[float, float] = (0.0, 0.05)
    peak_snr: tuple[float, float] = (20.0, 300.0)
    read_snr: tuple[float, float] = (200.0, 2000.0)


def _log_uniform(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return float(math.exp(rng.uniform(math.log(low), math.log(high))))


def sample_composition(
    rng: np.random.Generator,
    targets: tuple[str, ...],
    ranges: RandomizationRanges,
    *,
    force_present: tuple[str, ...] = (),
) -> tuple[dict[str, float], dict[str, int]]:
    masses: dict[str, float] = {}
    labels: dict[str, int] = {}
    for element in targets:
        present = element in force_present or rng.random() < ranges.presence_probability
        labels[element] = int(present)
        if not present:
            continue
        bounds = {
            "H": ranges.hydrogen_mass_fraction,
            "He": ranges.helium_mass_fraction,
        }.get(element, ranges.target_mass_fraction)
        masses[element] = _log_uniform(rng, bounds)
    for element, (probability, low, high) in ranges.interferents.items():
        if element in targets:
            continue
        if rng.random() < probability:
            masses[element] = _log_uniform(rng, (low, high))
    cations = sum(v for el, v in masses.items() if el not in {"H", "He", "C"})
    masses["O"] = max(ranges.oxygen_per_cation_mass * cations, 0.05)
    total = sum(masses.values())
    return {el: value / total for el, value in masses.items()}, labels


def smooth_random_curve(
    rng: np.random.Generator, grid: np.ndarray, std: float, order: int = 4
) -> np.ndarray:
    x = 2 * (grid - grid[0]) / (grid[-1] - grid[0]) - 1
    coefficients = np.r_[0.0, rng.normal(0, std, size=order)]
    return np.polynomial.legendre.legval(x, coefficients)


@dataclass
class PlasmaSample:
    intensity: np.ndarray
    clean: np.ndarray
    labels: dict[str, int]
    record: dict[str, Any]


def sample_conditions(rng: np.random.Generator, ranges: RandomizationRanges) -> PlasmaConditions:
    hot = PlasmaZone(
        float(rng.uniform(*ranges.hot_temperature_k)),
        _log_uniform(rng, ranges.hot_density_cm3),
        1.0,
    )
    cool = PlasmaZone(
        float(rng.uniform(*ranges.cool_temperature_k)),
        _log_uniform(rng, ranges.cool_density_cm3),
        float(rng.uniform(*ranges.cool_weight)),
    )
    return PlasmaConditions(
        zones=(hot, cool),
        heavy_density_cm3=_log_uniform(rng, ranges.heavy_density_cm3),
        path_length_cm=_log_uniform(rng, ranges.path_length_cm),
        stark_fwhm_nm=_log_uniform(rng, ranges.stark_fwhm_nm),
        stark_spread=ranges.stark_spread,
        continuum_factor=_log_uniform(rng, ranges.continuum_factor),
    )


def generate_plasma_sample(
    model: PlasmaModel,
    detector_grid_nm: np.ndarray,
    bands: tuple[tuple[float, float, tuple[float, float]], ...],
    rng: np.random.Generator,
    *,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    ranges: RandomizationRanges | None = None,
    composition: dict[str, float] | None = None,
    labels: dict[str, int] | None = None,
) -> PlasmaSample:
    ranges = ranges or RandomizationRanges()
    if composition is None:
        mass, labels = sample_composition(rng, targets, ranges)
    else:
        if labels is None:
            raise ValueError("Explicit composition requires explicit labels")
        mass = composition
    conditions = sample_conditions(rng, ranges)
    radiance = model.radiance(mass_to_number_fractions(mass), conditions, rng)
    instrument = tuple(
        InstrumentBand(start, end, float(rng.uniform(*fwhm))) for start, end, fwhm in bands
    )
    shift = float(rng.normal(0, ranges.shift_nm_std))
    stretch = float(rng.normal(0, ranges.stretch_std))
    clean = apply_instrument(
        model.grid_nm, radiance, detector_grid_nm, instrument, shift_nm=shift, stretch=stretch
    )
    observed_pixels = clean != 0
    clean = clean * np.exp(
        smooth_random_curve(rng, detector_grid_nm, ranges.response_coefficient_std)
    )
    peak = float(np.max(clean))
    if not np.isfinite(peak) or peak <= 0:
        raise ValueError("Simulated spectrum has no positive signal")
    clean = clean / peak
    baseline = float(rng.uniform(*ranges.baseline_level)) * (
        1 + smooth_random_curve(rng, detector_grid_nm, 0.5, order=3)
    )
    peak_snr = _log_uniform(rng, ranges.peak_snr)
    read_snr = _log_uniform(rng, ranges.read_snr)
    expected = clean + baseline
    noise_std = np.sqrt(np.maximum(expected, 0)) / peak_snr + 1 / read_snr
    observed = expected + rng.normal(0, 1, size=expected.shape) * noise_std
    observed[~observed_pixels] = 0.0
    record = {
        "mass_fractions": mass,
        "conditions": asdict(conditions),
        "instrument": [asdict(band) for band in instrument],
        "shift_nm": shift,
        "stretch": stretch,
        "peak_snr": peak_snr,
        "read_snr": read_snr,
        "labels": labels,
    }
    return PlasmaSample(observed, clean, dict(labels or {}), record)


def element_templates(
    model: PlasmaModel,
    detector_grid_nm: np.ndarray,
    elements: tuple[str, ...],
    *,
    temperature_k: float = 10000.0,
    electron_density_cm3: float = 1e17,
    bands: tuple[InstrumentBand, ...] | None = None,
    matrix: dict[str, float] | None = None,
) -> np.ndarray:
    conditions = PlasmaConditions(
        zones=(PlasmaZone(temperature_k, electron_density_cm3),),
        heavy_density_cm3=1e17,
        path_length_cm=0.1,
        continuum_factor=0.0,
    )
    base = matrix or {"O": 0.6, "Si": 0.2, "Al": 0.1, "Ca": 0.1}
    band = bands or (InstrumentBand(detector_grid_nm[0], detector_grid_nm[-1], 0.3),)
    rows = []
    for element in elements:
        fractions = {key: value for key, value in base.items() if key != element}
        fractions[element] = 1e-4
        trace = model.radiance(fractions, conditions)
        fractions_without = {key: value for key, value in fractions.items() if key != element}
        matrix_only = model.radiance(fractions_without, conditions)
        spectrum = apply_instrument(model.grid_nm, trace - matrix_only, detector_grid_nm, band)
        spectrum = np.maximum(spectrum, 0)
        scale = float(np.max(spectrum))
        if scale <= 0:
            raise ValueError(f"No lines of {element} inside the detector range")
        rows.append(spectrum / scale)
    return np.stack(rows)
