import math
from pathlib import Path

import numpy as np
import pytest
from spectralab_ml.config import repository_root
from spectralab_ml.data.plasma_reference import (
    BOLTZMANN_EV_PER_K,
    PlasmaReference,
    parse_nist_levels,
    parse_nist_lines,
)
from spectralab_ml.evaluation.plasma_validation import (
    make_plasma_dataset,
    operational_labels,
    operational_report,
)
from spectralab_ml.simulation.plasma import (
    SAHA_CM3,
    InstrumentBand,
    PlasmaConditions,
    PlasmaModel,
    PlasmaZone,
    RandomizationRanges,
    apply_instrument,
    debye_lowering_ev,
    element_templates,
    generate_plasma_sample,
    mass_to_number_fractions,
    sample_composition,
)

EV_NM = 1239.841984


def toy_reference() -> PlasmaReference:
    e1, e2 = EV_NM / 600.0, EV_NM / 420.0
    species = {
        "Na_1": {
            "element": "Na",
            "ion_stage": 1,
            "ionization_energy_ev": 5.139,
            "levels_ev": [0.0, e1, e2],
            "levels_g": [2.0, 4.0, 2.0],
        },
        "Na_2": {
            "element": "Na",
            "ion_stage": 2,
            "ionization_energy_ev": 47.29,
            "levels_ev": [0.0],
            "levels_g": [1.0],
        },
        "O_1": {
            "element": "O",
            "ion_stage": 1,
            "ionization_energy_ev": 13.618,
            "levels_ev": [0.0],
            "levels_g": [9.0],
        },
        "O_2": {
            "element": "O",
            "ion_stage": 2,
            "ionization_energy_ev": 35.121,
            "levels_ev": [0.0],
            "levels_g": [4.0],
        },
    }
    columns = {
        "element": ["Na", "Na"],
        "ion_stage": [1, 1],
        "wavelength_nm": [420.0, 600.0],
        "aki": [1e7, 1e7],
        "ei_ev": [0.0, 0.0],
        "ek_ev": [e2, e1],
        "gi": [2.0, 2.0],
        "gk": [2.0, 4.0],
    }
    return PlasmaReference.from_tables({"wavelength_medium": "vacuum"}, species, columns)


def thin_conditions(density: float = 1e10, temperature: float = 9000.0) -> PlasmaConditions:
    return PlasmaConditions(
        zones=(PlasmaZone(temperature, 1e17),),
        heavy_density_cm3=density,
        path_length_cm=0.1,
        stark_fwhm_nm=0.0,
        continuum_factor=0.0,
    )


def line_integral(model: PlasmaModel, radiance: np.ndarray, center: float) -> float:
    window = np.abs(model.grid_nm - center) < 2.0
    return float(np.sum(radiance[window]) * model.fine_step_nm)


def test_nist_parsers_keep_complete_transitions_and_lowest_limit():
    lines = (
        b"obs_wl_vac(nm),ritz_wl_vac(nm),intens,Aki(s^-1),Acc,Ei(eV),Ek(eV),J_i,J_k,g_i,g_k\n"
        b'"=""589.158""","=""589.1583""","=""80000""","=""6.16e+07""",AA,'
        b'"=""0.0""","=""2.104""","=""1/2""","=""3/2""",2,4\n'
        b'"=""590.0""","=""""","=""10""","=""""",,"=""""","=""""","=""""","=""""",1,1\n'
    )
    parsed, report = parse_nist_lines(lines, "Na", 1)
    assert [line["aki"] for line in parsed] == [6.16e7]
    assert report == {
        "rows": 2,
        "accepted": 1,
        "missing_level_data": 1,
        "invalid": 0,
        "no_data": False,
    }
    levels = (
        b"J,g,Prefix,Level (eV),Suffix,Reference\n"
        b'"=""1/2""",2,"=""""","=""0.000""","=""""",""\n'
        b'"=""3/2""",4,"=""""","=""[2.104]""","=""""",""\n'
        b'"=""---""",,"=""""","=""5.139""","=""""",""\n'
        b'"=""1/2""",2,"=""""","=""30.0""","=""""",""\n'
        b'"=""---""",,"=""""","=""71.3""","=""""",""\n'
        b'"=""1/2""",2,"=""""","=""1.5+x""","=""""",""\n'
    )
    bound, limit, report = parse_nist_levels(levels)
    assert limit == 5.139
    assert bound == [(0.0, 2.0), (2.104, 4.0)]
    assert parse_nist_lines(b"<!DOCTYPE html><html>no lines</html>", "K", 2)[1]["no_data"]


def test_saved_plasma_reference_has_nist_ionization_energies():
    path = repository_root() / "data/reference/nist_plasma_v3/plasma_reference.json"
    reference = PlasmaReference.load(path)
    assert reference.ionization_energy_ev("Na", 1) == pytest.approx(5.139, abs=1e-3)
    assert reference.ionization_energy_ev("Ca", 1) == pytest.approx(6.113, abs=1e-3)
    assert reference.ionization_energy_ev("Mg", 1) == pytest.approx(7.646, abs=1e-3)
    assert reference.partition_function("H", 1, 5000) == pytest.approx(2.0, abs=1e-3)
    assert reference.partition_function("H", 2, 9000) == 1.0
    hot = reference.partition_function("Na", 1, 15000)
    lowered = reference.partition_function("Na", 1, 15000, lowering_ev=0.2)
    assert 2.0 < lowered < hot


def test_saha_balance_matches_closed_form_and_trends():
    reference = toy_reference()
    model = PlasmaModel(reference, 400, 620)
    zone = PlasmaZone(10000, 1e17)
    densities = model.species_densities({"Na": 1.0}, zone, 1e16)
    lowering = debye_lowering_ev(10000, 1e17, 1)
    u1 = reference.partition_function("Na", 1, 10000, lowering_ev=lowering)
    ratio = (
        2
        * SAHA_CM3
        * 10000**1.5
        * (1.0 / u1)
        * math.exp(-(5.139 - lowering) / (BOLTZMANN_EV_PER_K * 10000))
        / 1e17
    )
    assert densities[("Na", 2)][0] / densities[("Na", 1)][0] == pytest.approx(ratio)
    assert sum(n for n, _ in densities.values()) == pytest.approx(1e16)
    cooler = model.species_densities({"Na": 1.0}, PlasmaZone(7000, 1e17), 1e16)
    denser = model.species_densities({"Na": 1.0}, PlasmaZone(10000, 1e18), 1e16)
    assert cooler[("Na", 1)][0] > densities[("Na", 1)][0]
    assert denser[("Na", 1)][0] > densities[("Na", 1)][0]


@pytest.mark.parametrize("temperature", [7000.0, 12000.0])
def test_optically_thin_lines_equal_boltzmann_emissivity(temperature):
    reference = toy_reference()
    model = PlasmaModel(reference, 400, 620)
    conditions = thin_conditions(temperature=temperature)
    radiance, tau = model.zone_radiance({"Na": 1.0}, conditions.zones[0], conditions)
    assert tau.max() < 1e-3
    n_neutral, u1 = model.species_densities({"Na": 1.0}, conditions.zones[0], 1e10)[("Na", 1)]
    kt = BOLTZMANN_EV_PER_K * temperature
    for wavelength, gk in ((600.0, 4.0), (420.0, 2.0)):
        ek = EV_NM / wavelength
        expected = 1e7 * gk * n_neutral * 1e6 * math.exp(-ek / kt) / u1 / (4 * math.pi) * 1e-3
        assert line_integral(model, radiance, wavelength) == pytest.approx(expected, rel=5e-3)


def test_self_absorption_saturates_strong_lines():
    reference = toy_reference()
    model = PlasmaModel(reference, 400, 620)
    integrals = []
    for density in (1e9, 1e11, 1e18):
        conditions = thin_conditions(density=density)
        radiance, _ = model.zone_radiance({"Na": 1.0}, conditions.zones[0], conditions)
        integrals.append(line_integral(model, radiance, 600.0) / density)
    assert integrals[1] == pytest.approx(integrals[0], rel=0.05)
    assert integrals[2] < 0.5 * integrals[0]


def test_instrument_conserves_area_and_applies_shift():
    grid = np.arange(400.0, 420.0, 0.005)
    radiance = np.exp(-0.5 * ((grid - 410) / 0.01) ** 2)
    detector = np.arange(402.0, 418.0, 0.1)
    band = (InstrumentBand(402, 418, 0.4),)
    observed = apply_instrument(grid, radiance, detector, band)
    assert observed.sum() * 0.1 == pytest.approx(radiance.sum() * 0.005, rel=1e-3)
    shifted = apply_instrument(grid, radiance, detector, band, shift_nm=0.3)

    def centroid(y):
        return float(np.sum(detector * y) / np.sum(y))

    assert centroid(shifted) == pytest.approx(centroid(observed) - 0.3, abs=0.01)
    partial = apply_instrument(grid, radiance, detector, (InstrumentBand(402, 409, 0.4),))
    assert np.all(partial[detector > 409] == 0)


def test_randomized_samples_are_reproducible_and_labelled():
    reference = toy_reference()
    model = PlasmaModel(reference, 400, 620)
    grid = np.round(np.arange(410.0, 610.0, 0.1), 3)
    ranges = RandomizationRanges(interferents={})
    bands = ((410, 500, (0.15, 0.3)), (500.05, 610, (0.4, 0.8)))
    first = generate_plasma_sample(
        model,
        grid,
        bands,
        np.random.default_rng(3),
        targets=("Na",),
        ranges=ranges,
        composition={"Na": 0.5, "O": 0.5},
        labels={"Na": 1},
    )
    second = generate_plasma_sample(
        model,
        grid,
        bands,
        np.random.default_rng(3),
        targets=("Na",),
        ranges=ranges,
        composition={"Na": 0.5, "O": 0.5},
        labels={"Na": 1},
    )
    np.testing.assert_array_equal(first.intensity, second.intensity)
    assert first.labels == {"Na": 1}
    assert first.clean.max() == pytest.approx(1.0)
    peak = grid[np.argmax(first.clean)]
    assert abs(peak - 600.0) < 1.5 or abs(peak - 420.0) < 1.5


def test_composition_sampling_respects_presence_labels():
    ranges = RandomizationRanges(presence_probability=0.5)
    rng = np.random.default_rng(0)
    for _ in range(50):
        mass, labels = sample_composition(rng, ("Na", "Fe", "H"), ranges)
        assert sum(mass.values()) == pytest.approx(1.0)
        for element, present in labels.items():
            assert (element in mass) == bool(present)
        assert "O" in mass
    fractions = mass_to_number_fractions({"Fe": 55.845, "O": 15.999})
    assert fractions["Fe"] == pytest.approx(0.5, abs=1e-3)


def test_element_templates_and_dataset_split():
    reference = toy_reference()
    model = PlasmaModel(reference, 400, 620)
    grid = np.round(np.arange(410.0, 610.0, 0.1), 3)
    templates = element_templates(model, grid, ("Na",), matrix={"Na": 1.0})
    assert templates.shape == (1, len(grid)) and templates.max() == pytest.approx(1.0)
    mask = grid < 600.0 + 5
    bundle = make_plasma_dataset(
        model,
        grid,
        mask,
        ((410, 610, (0.2, 0.3)),),
        ("Na",),
        {"content_sha256": "toy"},
        seed=5,
        base_samples=30,
        variations=2,
        ranges=RandomizationRanges(interferents={}),
        n_jobs=1,
    )
    bundle.validate()
    assert len(bundle.records) == 60
    assert np.all(bundle.train.X[:, ~mask] == 0)


def test_operational_labels_and_report():
    assert operational_labels(2.0) is True
    assert operational_labels(0.05) is False
    assert operational_labels(0.5) is None
    assert operational_labels(0.0) is None
    assert operational_labels(None) is None
    scores = np.asarray([[0.9], [0.8], [0.2], [0.3], [0.7], [0.1]])
    detections = scores >= 0.5
    labels = [[True], [True], [False], [False], [False], [None]]
    report = operational_report(scores, detections, labels, ["a", "b", "c", "d", "e", "f"], ("Na",))
    element = report["per_element"]["Na"]
    assert element["recall"]["value"] == 1.0
    assert element["false_positive_rate"]["value"] == pytest.approx(1 / 3)
    assert element["roc_auc"] == 1.0
    assert element["unknown_samples"] == 1
    with pytest.raises(ValueError, match="conflicting"):
        operational_report(scores[:2], detections[:2], [[True], [False]], ["a", "a"], ("Na",))


def test_reference_checksum_is_enforced(tmp_path: Path):
    path = repository_root() / "data/reference/nist_plasma_v3/plasma_reference.json"
    tampered = tmp_path / "plasma_reference.json"
    tampered.write_text(path.read_text().replace('"Na"', '"Ne"', 1))
    with pytest.raises(ValueError, match="checksum"):
        PlasmaReference.load(tampered)
