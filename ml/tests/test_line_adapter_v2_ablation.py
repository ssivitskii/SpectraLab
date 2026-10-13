"""Regression coverage: adding strong UV lines must preserve visible evidence."""

import numpy as np
from spectralab_ml.data.reference import ReferenceCatalog, SpectralLine
from spectralab_ml.models.line_adapter_v2 import CLASSES, extract_line_features, line_plan


def reference_fixture():
    lines = []
    for element in CLASSES:
        for stage, visible_start, uv_start in ((1, 390.0, 260.0), (2, 510.0, 290.0)):
            for start, strength in ((visible_start, 10.0), (uv_start, 1000.0)):
                for i in range(10):
                    wavelength = start + 1.5 * i
                    lines.append(
                        SpectralLine(
                            element=element,
                            ion_stage=stage,
                            wavelength_nm=wavelength,
                            wavelength_medium="vacuum",
                            wavelength_kind="observed",
                            relative_intensity=strength + i,
                            raw_intensity=str(strength + i),
                            flags="",
                            source_id="Synthetic reference fixture",
                            raw_wavelength=str(wavelength),
                            source_fields={},
                        )
                    )
    return ReferenceCatalog(lines, {"wavelength_medium": "vacuum", "content_sha256": "a" * 64})


def test_stronger_uv_transitions_cannot_displace_visible_reference_lines():
    catalog = reference_fixture()
    visible = line_plan(catalog, include_uv=False)
    combined = line_plan(catalog, include_uv=True)
    for element in CLASSES:
        old = set(visible["lines_nm"][element])
        new = set(combined["lines_nm"][element])
        assert len(old) == 16
        assert old < new
        assert len(new - old) == 16
        assert all(241 < wavelength < 340 for wavelength in new - old)
        assert {wavelength for wavelength in new if wavelength > 340} == old


def test_visible_feature_values_remain_identical_in_uv_additive_ablation():
    catalog = reference_fixture()
    visible = line_plan(catalog, include_uv=False)
    combined = line_plan(catalog, include_uv=True)
    grid = np.linspace(240, 900, 6601)
    signal = 1.0 + 0.2 * np.sin(grid)
    for line in catalog.for_elements(["Mg"]):
        signal += 3 * np.exp(-(((grid - line.wavelength_nm) / 0.15) ** 2))
    altered_uv = signal.copy()
    altered_uv[(grid >= 241) & (grid <= 340)] *= 1000
    spectra = np.stack([signal, altered_uv])
    old_features = extract_line_features(spectra, grid, visible)
    new_features = extract_line_features(spectra, grid, combined)
    for element in CLASSES:
        indices = [
            2 * combined["lines_nm"][element].index(wavelength) + feature
            for wavelength in visible["lines_nm"][element]
            for feature in (0, 1)
        ]
        np.testing.assert_array_equal(new_features[element][:, indices], old_features[element])
        np.testing.assert_array_equal(old_features[element][0], old_features[element][1])
