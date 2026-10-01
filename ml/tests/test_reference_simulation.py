import json
from dataclasses import replace

import numpy as np
import pytest
from spectralab_ml.config import repository_root
from spectralab_ml.data.reference import ReferenceCatalog, parse_relative_intensity
from spectralab_ml.simulation import GenerationConfig, generate_spectrum
from spectralab_ml.simulation.generator import pixel_integrated_gaussian


def test_real_nist_csv():
    root = repository_root() / "data" / "fixtures"
    meta = json.loads((root / "nist_na_i_vacuum_580_600.metadata.json").read_text())
    catalog, report = ReferenceCatalog.from_delimited(
        root / "nist_na_i_vacuum_580_600.csv", metadata=meta
    )
    assert report.accepted_rows == 2 and not report.dropped_rows
    assert all(
        line.wavelength_kind == "observed" and line.wavelength_medium == "vacuum"
        for line in catalog.lines
    )
    assert catalog.lines[0].relative_intensity == 80000
    assert catalog.lines[0].raw_wavelength and catalog.lines[0].source_fields


def test_missing_annotations_and_cache(tmp_path):
    path = tmp_path / "lines.csv"
    path.write_text(
        "element,ion_stage,wavelength_nm,wavelength_medium,wavelength_kind,relative_intensity,flags\nH,1,500,vacuum,observed,,\nNa,1,589,vacuum,observed,12bl,*\nFe,1,500.0001,vacuum,ritz,3,\nMg,1,550,air,observed,10,\nH,1,555,vacuum,observed,10,,extra\n"
    )
    cache = tmp_path / "cache.json"
    catalog, report = ReferenceCatalog.from_delimited(
        path, metadata={"source": "test"}, cache_path=cache, report_path=tmp_path / "report.json"
    )
    assert len(catalog.lines) == 3 and len(report.dropped_rows) == 2
    assert catalog.lines[0].relative_intensity is None
    assert "bl" in catalog.lines[-1].flags
    assert ReferenceCatalog.from_cache(cache).metadata["checksum_sha256"] == report.checksum_sha256
    assert parse_relative_intensity("weak")[0] is None


def test_reproducibility_labels_and_noise(catalog, classes):
    config = GenerationConfig(elements=("H", "Na"), component_weights=(1.0, 0.4), seed=173)
    first = generate_spectrum(config, catalog, class_order=classes)
    second = generate_spectrum(config, catalog, class_order=classes)
    np.testing.assert_array_equal(first.intensity, second.intensity)
    assert len(first.intensity) == 9001 and np.isfinite(first.intensity).all()
    assert list(first.true_labels) == list(classes) and sum(first.true_labels.values()) == 2
    assert np.any(first.intensity < 0)
    clean = generate_spectrum(replace(config, snr_db=None), catalog, class_order=classes)
    np.testing.assert_array_equal(clean.intensity, clean.clean_signal)
    json.dumps(clean.to_dict(), allow_nan=False)


def test_area_and_subpixel_lines():
    centers = np.arange(350.0, 801.0)
    for fwhm in [0.01, 0.2, 0.5, 2.0]:
        profile = pixel_integrated_gaussian(centers, 550.49, fwhm)
        assert np.isclose(profile.sum(), 1.0, atol=1e-12)
        assert profile.max() > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sampling_step_nm": 1e-10},
        {"snr_db": -10000},
        {"amplitude_variation": 1e308},
        {"background_level": float("nan")},
        {"sampling_step_nm": 0.17},
    ],
)
def test_generation_bounds(catalog, classes, kwargs):
    with pytest.raises(ValueError):
        generate_spectrum(
            GenerationConfig(elements=("H",), component_weights=(1.0,), **kwargs),
            catalog,
            class_order=classes,
        )
