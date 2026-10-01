from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_KINDS = {"observed", "ritz"}
INTENSITY_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
WAVELENGTH_HEADER = re.compile(
    r"^(?:(observed|ritz)\s+wavelength\s+(air|vac(?:uum)?)|(obs|ritz)_wl_(air|vac))\s*\(nm\)$",
    re.IGNORECASE,
)
ROMAN_STAGES = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


@dataclass(frozen=True)
class SpectralLine:
    element: str
    ion_stage: int
    wavelength_nm: float
    wavelength_medium: str
    wavelength_kind: str
    relative_intensity: float | None
    raw_intensity: str
    flags: str
    source_id: str
    raw_wavelength: str
    source_fields: dict[str, str]


@dataclass(frozen=True)
class ImportIssue:
    row_number: int
    reason: str
    raw: dict[str, str]


@dataclass
class ImportReport:
    accepted_rows: int
    dropped_rows: list[ImportIssue]
    duplicate_rows: int
    checksum_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_rows": self.accepted_rows,
            "dropped_rows": [asdict(issue) for issue in self.dropped_rows],
            "duplicate_rows": self.duplicate_rows,
            "checksum_sha256": self.checksum_sha256,
        }


def parse_relative_intensity(raw: str | None) -> tuple[float | None, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    match = INTENSITY_NUMBER.search(text.replace(",", "."))
    if match is None:
        return None, text
    value = float(match.group(0))
    if not math.isfinite(value) or value < 0:
        return None, text
    flags = f"intensity_annotation:{text}" if match.group(0) != text else ""
    return value, flags


def _clean_header(value: str) -> str:
    return " ".join(value.removeprefix("\ufeff").strip().split())


def _first_matching_header(headers: list[str], pattern: re.Pattern[str]) -> str | None:
    return next((header for header in headers if pattern.search(_clean_header(header))), None)


def _normalise_nist_row(
    row: dict[str, str], headers: list[str], metadata: dict[str, Any]
) -> dict[str, str]:
    normalised = {_clean_header(key).lower(): value for key, value in row.items()}
    if {"element", "ion_stage", "wavelength_nm", "wavelength_medium"} <= normalised.keys():
        return normalised

    ion_header = _first_matching_header(headers, re.compile(r"^ion$", re.IGNORECASE))
    intensity_header = _first_matching_header(
        headers, re.compile(r"^(?:rel\.?\s*int\.?|intens)$", re.IGNORECASE)
    )
    wavelength_columns: list[tuple[str, str, str]] = []
    for header in headers:
        match = WAVELENGTH_HEADER.match(_clean_header(header))
        if match:
            raw_kind = (match.group(1) or match.group(3)).lower()
            raw_medium = (match.group(2) or match.group(4)).lower()
            wavelength_columns.append(
                (header, "observed" if raw_kind == "obs" else raw_kind, raw_medium)
            )
    if not wavelength_columns:
        raise ValueError("unrecognised NIST headers")
    if ion_header is not None:
        ion_token = (row.get(ion_header) or "").strip()
        ion_match = re.fullmatch(r"([A-Z][a-z]?)\s+([IV]+)", ion_token)
        if ion_match is None or ion_match.group(2) not in ROMAN_STAGES:
            raise ValueError("Ion must look like 'Fe I'")
        element = ion_match.group(1)
        ion_stage = ROMAN_STAGES[ion_match.group(2)]
    else:
        element = str(metadata.get("element", ""))
        ion_stage = int(metadata.get("ion_stage", 0))
        if not element or ion_stage <= 0:
            raise ValueError("single-spectrum export requires metadata element and ion_stage")
    # One transition may contain observed and Ritz columns. Prefer observed when populated.
    populated = [item for item in wavelength_columns if (row.get(item[0]) or "").strip()]
    if not populated:
        raise ValueError("row has no observed or Ritz wavelength")
    selected = next((item for item in populated if item[1] == "observed"), populated[0])
    header, kind, medium = selected
    return {
        "element": element,
        "ion_stage": str(ion_stage),
        "wavelength_nm": (row.get(header) or "").strip(),
        "wavelength_medium": "vacuum" if medium.startswith("vac") else "air",
        "wavelength_kind": kind,
        "relative_intensity": (row.get(intensity_header) or "") if intensity_header else "",
        "flags": "",
        "source_id": "NIST ASD saved export",
    }


def _parse_strict_number(raw: str) -> tuple[float, str]:
    text = raw.strip()
    formula = re.fullmatch(r'=\s*"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"', text)
    if formula:
        return float(formula.group(1)), "excel_formula"
    if text.startswith("="):
        raise ValueError("malformed spreadsheet formula")
    if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text):
        raise ValueError("invalid numeric wavelength")
    return float(text), ""


class ReferenceCatalog:
    def __init__(self, lines: list[SpectralLine], metadata: dict[str, Any]):
        self.lines = lines
        self.metadata = metadata

    @property
    def source_name(self) -> str:
        return str(self.metadata.get("source", "unknown"))

    def for_elements(self, elements: list[str] | tuple[str, ...]) -> list[SpectralLine]:
        wanted = set(elements)
        return [line for line in self.lines if line.element in wanted]

    @classmethod
    def from_delimited(
        cls,
        path: Path,
        *,
        metadata: dict[str, Any],
        cache_path: Path | None = None,
        report_path: Path | None = None,
    ) -> tuple[ReferenceCatalog, ImportReport]:
        payload = path.read_bytes()
        checksum = hashlib.sha256(payload).hexdigest()
        sample = payload[:4096].decode("utf-8-sig", errors="replace")
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
        except csv.Error:
            delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        issues: list[ImportIssue] = []
        candidates: list[tuple[int, SpectralLine]] = []
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=delimiter)
            headers = list(reader.fieldnames or [])
            if len({_clean_header(header).lower() for header in headers}) != len(headers):
                raise ValueError("Duplicate column headers")
            canonical_headers = {_clean_header(header).lower() for header in headers}
            is_canonical = {
                "element",
                "ion_stage",
                "wavelength_nm",
                "wavelength_medium",
            } <= canonical_headers
            if not is_canonical and not any(
                WAVELENGTH_HEADER.match(_clean_header(h)) for h in headers
            ):
                raise ValueError("Missing canonical or recognised NIST wavelength columns")
            for row_number, row in enumerate(reader, start=2):
                raw_row = {str(key): str(value) for key, value in row.items()}
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("row has an unexpected field count")
                    row = _normalise_nist_row(row, headers, metadata)
                    medium = row["wavelength_medium"].strip().lower()
                    if medium != "vacuum":
                        raise ValueError("only vacuum wavelengths are accepted")
                    kind = (row.get("wavelength_kind") or "observed").strip().lower()
                    if kind not in ALLOWED_KINDS:
                        raise ValueError("wavelength_kind must be observed or ritz")
                    wavelength, wavelength_flag = _parse_strict_number(row["wavelength_nm"])
                    ion_stage = int(row["ion_stage"])
                    element = row["element"].strip()
                    if not re.fullmatch(r"[A-Z][a-z]?", element):
                        raise ValueError("element must be a valid chemical symbol")
                    if ion_stage <= 0:
                        raise ValueError("ion_stage must be positive")
                    if not math.isfinite(wavelength) or wavelength <= 0:
                        raise ValueError("wavelength must be a positive finite number")
                    intensity, parsed_flags = parse_relative_intensity(
                        row.get("relative_intensity")
                    )
                    flags = ";".join(
                        item
                        for item in (
                            (row.get("flags") or "").strip(),
                            parsed_flags,
                            wavelength_flag,
                        )
                        if item
                    )
                    candidates.append(
                        (
                            row_number,
                            SpectralLine(
                                element=element,
                                ion_stage=ion_stage,
                                wavelength_nm=wavelength,
                                wavelength_medium=medium,
                                wavelength_kind=kind,
                                relative_intensity=intensity,
                                raw_intensity=(row.get("relative_intensity") or "").strip(),
                                flags=flags,
                                source_id=(
                                    row.get("source_id") or metadata.get("source", "unknown")
                                ),
                                raw_wavelength=row["wavelength_nm"],
                                source_fields=raw_row,
                            ),
                        )
                    )
                except (AttributeError, TypeError, ValueError, KeyError) as exc:
                    issues.append(ImportIssue(row_number, str(exc), raw_row))

        # A single NIST row carrying observed and Ritz already yielded one record above.
        # Only byte-for-byte equivalent normalized rows are treated as duplicates; close
        # physical lines remain distinct.
        selected: dict[tuple[str, int, float, str, str], tuple[int, SpectralLine]] = {}
        duplicate_rows = 0
        for candidate in candidates:
            row_number, line = candidate
            key = (
                line.element,
                line.ion_stage,
                line.wavelength_nm,
                line.wavelength_kind,
                line.source_id,
            )
            existing = selected.get(key)
            if existing is None:
                selected[key] = candidate
            else:
                duplicate_rows += 1
                issues.append(
                    ImportIssue(row_number, "exact duplicate transition", line.source_fields)
                )

        lines = sorted((item[1] for item in selected.values()), key=lambda x: x.wavelength_nm)
        enriched_metadata = {
            **metadata,
            "checksum_sha256": checksum,
            "imported_at": datetime.now(UTC).isoformat(),
            "units": "nm",
            "wavelength_medium": "vacuum",
            "cleaning_rules": (
                "reject non-vacuum/invalid; observed preferred within one transition row; "
                "only exact duplicates removed; raw fields retained"
            ),
        }
        report = ImportReport(len(lines), issues, duplicate_rows, checksum)
        catalog = cls(lines, enriched_metadata)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {"metadata": enriched_metadata, "lines": [asdict(line) for line in lines]},
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
        return catalog, report

    @classmethod
    def from_cache(cls, path: Path) -> ReferenceCatalog:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lines = []
        for row in payload["lines"]:
            row.setdefault("raw_wavelength", str(row["wavelength_nm"]))
            row.setdefault("source_fields", {})
            lines.append(SpectralLine(**row))
        return cls(lines, payload["metadata"])


def load_demo_catalog(root: Path | None = None) -> ReferenceCatalog:
    from spectralab_ml.config import repository_root

    project_root = root or repository_root()
    fixture = project_root / "data" / "fixtures" / "demo_lines.csv"
    metadata = json.loads(
        (project_root / "data" / "fixtures" / "demo_lines.metadata.json").read_text(
            encoding="utf-8"
        )
    )
    catalog, _ = ReferenceCatalog.from_delimited(fixture, metadata=metadata)
    return catalog
