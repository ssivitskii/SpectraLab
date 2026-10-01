"""Inspect the actual local catalog; outputs are research artifacts, not NIST claims."""

from dataclasses import asdict
from itertools import combinations

import matplotlib.pyplot as plt
import pandas as pd
from spectralab_ml.config import load_project_config, repository_root
from spectralab_ml.data.reference import load_demo_catalog

root = repository_root()
project = load_project_config()
catalog = load_demo_catalog()
out = root / "reports" / "analytics" / "01_reference_data"
out.mkdir(parents=True, exist_ok=True)
frame = pd.DataFrame([asdict(line) for line in catalog.for_elements(project.elements)])
frame["in_range"] = frame.wavelength_nm.between(
    project.wavelength_start_nm, project.wavelength_end_nm
)
summary = frame.groupby("element").agg(
    lines=("wavelength_nm", "size"),
    first_nm=("wavelength_nm", "min"),
    last_nm=("wavelength_nm", "max"),
    lines_in_range=("in_range", "sum"),
    missing_intensity=("relative_intensity", lambda values: int(values.isna().sum())),
)
summary.to_csv(out / "coverage.csv")
near = [
    {
        "element_a": a.element,
        "wavelength_a_nm": a.wavelength_nm,
        "element_b": b.element,
        "wavelength_b_nm": b.wavelength_nm,
        "separation_nm": abs(a.wavelength_nm - b.wavelength_nm),
    }
    for a, b in combinations(catalog.lines, 2)
    if abs(a.wavelength_nm - b.wavelength_nm) <= 1.0
]
pd.DataFrame(
    near, columns=["element_a", "wavelength_a_nm", "element_b", "wavelength_b_nm", "separation_nm"]
).to_csv(out / "potential_overlap_1nm.csv", index=False)
fig, axes = plt.subplots(1, 3, figsize=(13, 4))
summary.lines.plot.bar(ax=axes[0], title="Line count", color="#1d4ed8")
for index, element in enumerate(project.elements):
    rows = frame[frame.element == element]
    axes[1].scatter(rows.wavelength_nm, [index] * len(rows), marker="|")
axes[1].set(
    yticks=range(len(project.elements)),
    yticklabels=project.elements,
    xlim=(project.wavelength_start_nm, project.wavelength_end_nm),
    xlabel="Vacuum wavelength, nm",
    title="Coverage",
)
axes[2].hist(frame.relative_intensity.dropna(), bins=12, color="#0f766e")
axes[2].set(xlabel="Relative intensity prior", title="Intensity distribution")
fig.suptitle(f"Reference source: {catalog.source_name}; proximity does not prove blending")
fig.tight_layout()
fig.savefig(out / "reference.png", dpi=150)
plt.close(fig)
print(out)
