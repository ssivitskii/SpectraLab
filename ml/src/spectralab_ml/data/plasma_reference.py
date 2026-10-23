from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

BOLTZMANN_EV_PER_K = 8.617333262e-5

PLASMA_SPECIES: tuple[tuple[str, int, str], ...] = (
    ("H", 1, "I"),
    ("He", 1, "I"),
    ("He", 2, "II"),
    *(
        (element, stage, roman)
        for element in ("Na", "Mg", "Ca", "Fe", "Si", "Al", "K", "Ti", "Mn", "O", "C")
        for stage, roman in ((1, "I"), (2, "II"))
    ),
)
ATOMIC_MASS_U = {
    "H": 1.008,
    "He": 4.0026,
    "C": 12.011,
    "O": 15.999,
    "Na": 22.990,
    "Mg": 24.305,
    "Al": 26.982,
    "Si": 28.085,
    "K": 39.098,
    "Ca": 40.078,
    "Ti": 47.867,
    "Mn": 54.938,
    "Fe": 55.845,
}
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _cell(raw: str | None) -> str:
    text = (raw or "").strip()
    match = re.fullmatch(r'=\s*"(.*)"', text)
    return (match.group(1) if match else text).strip()


def _number(raw: str | None) -> float | None:
    text = re.sub(r"[\[\]()?]", "", _cell(raw)).strip()
    if not text or re.search(r"[A-DF-Za-df-z]", text) or not _NUMBER.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _leading_number(raw: str | None) -> float | None:
    match = _NUMBER.match(_cell(raw))
    return float(match.group(0)) if match else None


def _is_html(payload: bytes) -> bool:
    return payload.lstrip()[:20].lower().startswith((b"<!doctype", b"<html"))


def parse_nist_lines(payload: bytes, element: str, ion_stage: int) -> tuple[list[dict], dict]:
    report = {"rows": 0, "accepted": 0, "missing_level_data": 0, "invalid": 0, "no_data": False}
    if _is_html(payload):
        report["no_data"] = True
        return [], report
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    lines = []
    for row in reader:
        report["rows"] += 1
        observed = _number(row.get("obs_wl_vac(nm)"))
        ritz = _number(row.get("ritz_wl_vac(nm)"))
        wavelength = observed if observed is not None else ritz
        aki = _number(row.get("Aki(s^-1)"))
        ei, ek = _number(row.get("Ei(eV)")), _number(row.get("Ek(eV)"))
        gi, gk = _number(row.get("g_i")), _number(row.get("g_k"))
        if wavelength is None or wavelength <= 0:
            report["invalid"] += 1
            continue
        if None in (aki, ei, ek, gi, gk) or aki <= 0 or gi <= 0 or gk <= 0 or ek <= ei:
            report["missing_level_data"] += 1
            continue
        lines.append(
            {
                "element": element,
                "ion_stage": ion_stage,
                "wavelength_nm": wavelength,
                "wavelength_kind": "observed" if observed is not None else "ritz",
                "aki": aki,
                "ei_ev": ei,
                "ek_ev": ek,
                "gi": gi,
                "gk": gk,
                "relative_intensity": _leading_number(row.get("intens")),
            }
        )
    report["accepted"] = len(lines)
    return lines, report


def parse_nist_levels(payload: bytes) -> tuple[list[tuple[float, float]], float | None, dict]:
    report = {"rows": 0, "accepted": 0, "skipped": 0, "no_data": False}
    if _is_html(payload):
        report["no_data"] = True
        return [], None, report
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    levels: list[tuple[float, float]] = []
    limits: list[float] = []
    for row in reader:
        report["rows"] += 1
        energy = _number(row.get("Level (eV)"))
        if _cell(row.get("J")) == "---":
            if energy is not None:
                limits.append(energy)
            continue
        g = _number(row.get("g"))
        if energy is None or g is None or g <= 0:
            report["skipped"] += 1
            continue
        levels.append((energy, g))
    limit = min(limits) if limits else None
    if limit is not None:
        bound = [(energy, g) for energy, g in levels if energy < limit]
        report["skipped"] += len(levels) - len(bound)
        levels = bound
    report["accepted"] = len(levels)
    return sorted(levels), limit, report


def build_plasma_reference(directory: Path, sources: list[dict]) -> dict[str, Any]:
    lines: list[dict] = []
    species: dict[str, dict] = {}
    report: dict[str, Any] = {"files": {}}
    for source in sources:
        payload = (directory / source["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise ValueError(f"Checksum mismatch: {source['file']}")
        key = f"{source['element']}_{source['ion_stage']}"
        if source["kind"] == "lines":
            parsed, file_report = parse_nist_lines(payload, source["element"], source["ion_stage"])
            lines.extend(parsed)
        else:
            levels, limit, file_report = parse_nist_levels(payload)
            if not levels:
                raise ValueError(f"No bound levels for {key}")
            if limit is None:
                raise ValueError(f"No ionization limit for {key}")
            species[key] = {
                "element": source["element"],
                "ion_stage": source["ion_stage"],
                "ionization_energy_ev": limit,
                "levels_ev": [level for level, _ in levels],
                "levels_g": [g for _, g in levels],
            }
        report["files"][source["file"]] = file_report
    missing = {f"{e}_{s}" for e, s, _ in PLASMA_SPECIES} - species.keys()
    if missing:
        raise ValueError(f"Missing level data: {sorted(missing)}")
    lines.sort(key=lambda item: (item["wavelength_nm"], item["element"], item["ion_stage"]))
    report["totals"] = {
        "transitions": len(lines),
        "species": len(species),
        "by_species": {
            key: sum(1 for line in lines if f"{line['element']}_{line['ion_stage']}" == key)
            for key in sorted(species)
        },
    }
    metadata = {
        "schema_version": 1,
        "source": "nist_asd_saved_export",
        "source_version": "plasma-v3",
        "units": {"wavelength": "nm", "energy": "eV", "aki": "s^-1"},
        "wavelength_medium": "vacuum",
        "wavelength_range_nm": [240, 900],
        "sources": sources,
        "selection": (
            "transitions with complete A_ki, E_i, E_k, g_i, g_k; observed wavelength "
            "preferred, Ritz otherwise; levels below the NIST ionization limit"
        ),
        "notice": "Atomic data for LTE simulation; not composition labels.",
    }
    return {"metadata": metadata, "species": species, "lines": lines, "report": report}


def _content_hash(metadata: dict, species: dict, columns: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {"metadata": metadata, "species": species, "lines": columns},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


LINE_COLUMNS = (
    "element",
    "ion_stage",
    "wavelength_nm",
    "aki",
    "ei_ev",
    "ek_ev",
    "gi",
    "gk",
)


def write_plasma_reference(directory: Path, reference: dict[str, Any]) -> Path:
    columns = {name: [line[name] for line in reference["lines"]] for name in LINE_COLUMNS}
    metadata = dict(reference["metadata"])
    metadata["content_sha256"] = _content_hash(metadata, reference["species"], columns)
    path = directory / "plasma_reference.json"
    path.write_text(
        json.dumps(
            {"metadata": metadata, "species": reference["species"], "lines": columns},
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    (directory / "import_report.json").write_text(
        json.dumps(reference["report"], indent=2, allow_nan=False) + "\n"
    )
    return path


@dataclass
class PlasmaReference:
    metadata: dict[str, Any]
    species: dict[str, dict[str, Any]]
    element: np.ndarray
    ion_stage: np.ndarray
    wavelength_nm: np.ndarray
    aki: np.ndarray
    ei_ev: np.ndarray
    ek_ev: np.ndarray
    gi: np.ndarray
    gk: np.ndarray
    _levels: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path) -> PlasmaReference:
        payload = json.loads(path.read_text())
        metadata, species, columns = payload["metadata"], payload["species"], payload["lines"]
        expected = metadata.get("content_sha256")
        stable = {k: v for k, v in metadata.items() if k != "content_sha256"}
        if expected != _content_hash(stable, species, columns):
            raise ValueError("Plasma reference content checksum mismatch")
        return cls.from_tables(metadata, species, columns)

    @classmethod
    def from_tables(
        cls, metadata: dict[str, Any], species: dict[str, dict], columns: dict[str, list]
    ) -> PlasmaReference:
        if metadata.get("wavelength_medium") != "vacuum":
            raise ValueError("Plasma reference must use vacuum wavelengths")
        arrays = {
            name: np.asarray(columns[name], dtype=object if name == "element" else float)
            for name in LINE_COLUMNS
        }
        arrays["ion_stage"] = arrays["ion_stage"].astype(int)
        arrays["element"] = arrays["element"].astype(str)
        if not (
            np.all(arrays["aki"] > 0)
            and np.all(arrays["gk"] > 0)
            and np.all(arrays["gi"] > 0)
            and np.all(arrays["ek_ev"] > arrays["ei_ev"])
        ):
            raise ValueError("Invalid transition data in plasma reference")
        for array in arrays.values():
            array.setflags(write=False)
        reference = cls(metadata=metadata, species=species, **arrays)
        for key, value in species.items():
            reference._levels[key] = (
                np.asarray(value["levels_ev"], dtype=float),
                np.asarray(value["levels_g"], dtype=float),
            )
        return reference

    @property
    def elements(self) -> tuple[str, ...]:
        return tuple(sorted({value["element"] for value in self.species.values()}))

    def ionization_energy_ev(self, element: str, ion_stage: int = 1) -> float:
        return float(self.species[f"{element}_{ion_stage}"]["ionization_energy_ev"])

    def partition_function(
        self,
        element: str,
        ion_stage: int,
        temperature_k: float,
        *,
        lowering_ev: float = 0.0,
    ) -> float:
        if not math.isfinite(temperature_k) or temperature_k <= 0:
            raise ValueError("Temperature must be positive and finite")
        if not math.isfinite(lowering_ev) or lowering_ev < 0:
            raise ValueError("Ionization lowering must be nonnegative and finite")
        key = f"{element}_{ion_stage}"
        if key not in self._levels:
            if (element, ion_stage) in {("H", 2), ("He", 3)}:
                return 1.0
            raise KeyError(f"No level data for {element} {ion_stage}")
        energy, g = self._levels[key]
        bound = energy < self.ionization_energy_ev(element, ion_stage) - lowering_ev
        bound[0] = True
        return float(
            np.sum(g[bound] * np.exp(-energy[bound] / (BOLTZMANN_EV_PER_K * temperature_k)))
        )
