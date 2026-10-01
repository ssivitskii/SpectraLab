"""Controlled examples using exactly the same generator as API and training."""

import json

import matplotlib.pyplot as plt
from spectralab_ml.config import load_project_config, repository_root
from spectralab_ml.data.reference import load_demo_catalog
from spectralab_ml.simulation import GenerationConfig, generate_spectrum

project = load_project_config()
catalog = load_demo_catalog()
out = repository_root() / "reports" / "analytics" / "02_synthetic_spectra"
out.mkdir(parents=True, exist_ok=True)
first, second, third = project.elements[:3]
base = dict(
    elements=(first, second),
    component_weights=(1, 0.7),
    seed=42,
    wavelength_start_nm=project.wavelength_start_nm,
    wavelength_end_nm=project.wavelength_end_nm,
)
scenarios = [
    ("Noise", [(f"SNR {snr}", {"snr_db": snr}) for snr in (None, 10, 30)]),
    (
        "Instrument broadening",
        [
            (f"FWHM {width} nm", {"snr_db": None, "instrumental_fwhm_nm": width})
            for width in (0.2, 0.5, 2.0)
        ],
    ),
    (
        "Detector sampling",
        [
            (f"Step {step} nm", {"snr_db": None, "sampling_step_nm": step})
            for step in (0.05, 0.5, 1.0)
        ],
    ),
    (
        "Mixtures",
        [
            ("single", {"elements": (first,), "component_weights": (1,)}),
            ("pair", {}),
            ("triple", {"elements": (first, second, third), "component_weights": (1, 0.7, 0.4)}),
        ],
    ),
    (
        "Weak second component",
        [
            (f"weight {weight}", {"snr_db": None, "component_weights": (1, weight)})
            for weight in (1, 0.1, 0.01)
        ],
    ),
]
fig, axes = plt.subplots(len(scenarios), 1, figsize=(12, 15))
records = []
for axis, (title, conditions) in zip(axes, scenarios, strict=True):
    for label, changes in conditions:
        spectrum = generate_spectrum(
            GenerationConfig(**(base | changes)), catalog, class_order=project.elements
        )
        axis.plot(spectrum.wavelength_nm, spectrum.intensity, linewidth=0.8, label=label)
        records.append({"scenario": title, "label": label, **spectrum.to_dict()})
    axis.set(title=title, xlabel="Vacuum wavelength, nm", ylabel="Intensity, a.u.")
    axis.legend(loc="upper left", fontsize=8)
fig.suptitle(f"Synthetic source: {catalog.source_name}; component weights are not concentrations")
fig.tight_layout()
fig.savefig(out / "conditions.png", dpi=150)
plt.close(fig)
(out / "spectra.json").write_text(json.dumps(records, allow_nan=False), encoding="utf-8")
print(out)
