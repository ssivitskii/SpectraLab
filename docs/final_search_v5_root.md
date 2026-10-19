# Additional independent-control source audit · 2026-10-19

This metadata-only search supplements the [v3 audit](final_validation_search_v3.md).
No candidate here has passed independent-final admission. No numerical spectral
member was opened. The [source registry](../data/reference/final_search_v5_root.json)
contains primary URLs, saved metadata hashes and specific unresolved requirements.

| Lead | New evidence | Remaining admission requirements |
| --- | --- | --- |
| [Capella et al. 2026](https://doi.org/10.1038/s41598-026-46449-2), MBH aluminum CRMs | Table 2 reports Fe 0.054 ± 0.007 wt% in G77J6 and Mg 0.010 ± 0.001 wt% in cp-Al-11697. The paper identifies certified compositions. | The uncertainty convention and actual certificates/lots need checking before converting these entries to qualified upper bounds. Their role as method/matrix controls, exported air/vacuum convention and acquisition independence are not established. The instrument/250–850 nm coverage differs from the current profile. |
| [Associated software deposit](https://doi.org/10.5281/zenodo.15967233) | MIT license; verified ZIP central-directory inventory contains only source/notebook/README/license. README requires the user to provide spectral CSVs. | The checked deposit does not provide the spectral CSVs. No undocumented data license or raw-data availability is inferred from the article's license. |
| [Cao robotic LIBS deposit](https://doi.org/10.5281/zenodo.17796049) | CC-BY-4.0; file inventory identifies raw LIBS data. | Metadata does not establish certified Fe/Mg controls, explicit exported axis convention or a frozen transfer from the other instrument. Spatial scans are not independent chemical assays. |
| [Sarawak paddy-soil deposit](https://doi.org/10.5281/zenodo.20437121) | CC-BY-4.0; 450 measurements from 15 soils, with sample-level classification scripts. | Cultivation-system labels do not establish Fe/Mg chemistry or certified blanks. Axis, instrument transfer and independent control support remain unresolved. |

The code archive was inspected by HTTP byte ranges for its central directory and
README only; no spectral members exist in that inventory. The linked GitHub tree
returned 404 at the checked URL. That does not prove that data cannot exist elsewhere.

A boric-acid substrate preprint was a search-index lead only: direct retrieval
returned 403. Its nominal 99.7% purity cannot by itself bound either Fe or Mg below
the operational limit. It is not counted as a fully verified data source.
