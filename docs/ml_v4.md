# ML v4: development evidence and guarded final evaluation

This iteration adds measured-spectrum workflows, conservative chemistry intake,
stronger training validation and a guarded independent-final protocol. The model
remains **development-only**. No independent final campaign or certified Fe/Mg
blank evaluation has been completed.

The new workflow reuses the disclosed development pool; it does not relabel the
former v1 test as untouched data. Existing v1/v2 study implementations and frozen
artifacts retain their separate identities.

In the follow-up plan, NEXT04–NEXT06 are implemented as software, and NEXT11 is
complete as a development calibration study with Brier/ECE, reliability and
partition provenance. Completing that study does not require a positive
calibration effect. NEXT09–NEXT10 remain partial; independent-final qualification
remains the separate NEXT08 requirement.

## Chemistry intake

[`normalize-assays`](../ml/src/spectralab_ml/data/assays.py) converts explicit mass
assays to FeOT or MgO weight percent. Supported units are `wt_percent`,
`mass_fraction`, `mg_per_kg`, `ug_per_g` and `ppm_mass`. An unspecified `ppm`, a
solution-volume concentration or surface loading cannot silently become a bulk
mass fraction. Elemental Fe and partial Fe oxide reports require an explicit
total-Fe declaration; FeOT and Fe2O3T carry total-Fe semantics.

Operational labels require the entire reported interval to lie in one region:
`0 < x <= 0.1` oxide wt% is negative and `x >= 1` is positive. Zero, missing values,
the intermediate region and intervals crossing a boundary remain unknown.
Informational or estimated assays also remain unknown. An explicit expanded
absolute uncertainty can be propagated, but its coverage factor is not promoted
to an inferred confidence level or purity certificate.

Every normalized row retains its input, input checksum, conversion factor and
qualification. `qualified_blank` stays false: chemistry normalization alone does
not establish an independent certified control, source license or compatible
spectrum. The checked examples and their output are
[the assay manifest](../data/reference/assay_normalization_examples.json) and
[the normalization summary](assay_normalization_v4_summary.json).

## Core training fixes

- Shared numeric validation rejects empty, misaligned or nonfinite matrices and
  nonbinary labels. CNN input is checked against the finite float32 range before
  conversion.
- Preprocessing requires a finite positive ordered wavelength range, a step that
  exactly divides the range, 2–100,000 grid points and a supported normalization.
- Dataset validation checks feature/class dimensions, unique class names and
  base-sample IDs. A base sample cannot cross train/validation/test boundaries,
  and its variations must have consistent labels. Training also requires
  generation provenance and one declared ion stage.
- Random-forest probabilities now handle a single output as well as multiple
  outputs, including a class with no positive training examples.
- Synthetic-workflow F1 threshold selection evaluates every distinct score in
  each class, preserving tied observations and the lowest-threshold tie rule.
  A single-class validation column retains its explicit fallback threshold.
- Refitting a calibrated estimator clears the old calibrators. Calibration
  inputs and reliability metrics receive the same shape/finite/binary checks;
  calibrators are replaced only after fitting the complete new set.

New generic training records threshold-selection schema version 2. Historical
reports keep their original provenance; a run with changed generic implementation
files is not the same implementation replay. The pinned v2 model/study files and
its numeric profile remain unchanged.

These correctness fixes are separate from the measured development study's
FPR-constrained threshold selection. Neither rule uses final-test outcomes.

## Measured development result

The locked primary study uses 202 spectra from 199 physical materials in 46
connected groups. Five outer folds keep the transitive closure of physical
material, family, session and input checksum together. Each outer training pool
has separate fit, calibration and tuning groups. Scalers and balanced logistic
models use known fit labels; monotonic Platt calibration uses only calibration
groups. C, decision thresholds and refusal parameters are selected on tuning
groups. Outer predictions are used only for reporting.

The fixed primary candidate adds local continuum/SNR features and neutral plus
singly ionized line summaries. It retains UV and visible coverage in
241–340, 383–468 and 474–900 nm. These features are proxies, not a measured
instrument-response correction or plasma-temperature estimate.

| Development comparison | Result |
| --- | --- |
| Primary macro calibrated Brier, v2-feature baseline → I + II candidate | 0.07407 → 0.05282 |
| Paired candidate-minus-baseline Brier difference | −0.02125; 95% interval [−0.03070, −0.01143] |
| Candidate calibration-only Brier difference, calibrated minus raw | Approximately −0.00060; 95% interval approximately [−0.005, 0.003] |
| Candidate Ca outer-fold FPR | 4/51 = 7.84% |
| Five-seed calibrated Brier, baseline → candidate | 0.084 ± 0.016 → 0.066 ± 0.012 |

The feature-package comparison improves Brier in this primary development run.
The interval for calibration alone crosses zero, so a separate calibration
benefit is not established. Ca FPR exceeds the 5% empirical tuning budget; that
budget is not a guaranteed bound on held-out or future errors. Primary refusal
rejects one known-negative Na material and no known positives, providing no
demonstration of improved selective safety.

Primary intervals use 1,000 paired connected-cluster bootstrap resamples of fixed
outer-fold predictions, without model refitting or multiple-comparison correction.
The five-seed values are mean ± sample standard deviation on the **same data**,
including four post-primary sensitivity runs. They are neither confidence
intervals nor five independent datasets. The primary profile was fixed before
outer scoring and was not replaced by a better-looking sensitivity run.

Full support, campaign slices, calibration detail and hashes are in
[the development report](development_v3.md) and
[its compact summary](development_v3_summary.json). The original numeric profile
is local at
`reports/adaptation_v3/development-20261019T172235-da8f0847/profile.json`, SHA-256
`8ffb76657c9618ff5c393b8be99690b2ceb41528d65529ebab31437caaf37c80`.
Historical v3 reports, trained profiles and raw spectra are ignored local artifacts;
a fresh clone contains the tracked plans and summaries, not those v3 model bytes.
The later frozen [Ca v4 research profile](../reports/adaptation_v4/calcium-zero-20261019T190232-12a3f9cb/research_profile.json)
is published as an explicit exception and embeds its original v3 state. Safe
profile loading validates bounded numeric JSON, dimensions, semantics and digest
without pickle.

## Offline checks and measured inference

`ml-check` checks strict configurations, reference-cache integrity, dependency
availability and optionally local evidence snapshots. It performs no network
requests and opens no numerical spectra. Missing optional CNN support and absent
ignored source downloads are reported separately from corrupt files. A successful
doctor report is an environment/integrity result, not model validation. The saved
check is [the environment summary](ml_environment_v4_summary.json).

`instrument-predict` accepts a numeric line-v3 profile and a measured CSV or
ChemCam CCS file. CSV input must have exactly `wavelength_nm,intensity`, finite
values, a strictly increasing axis and at most 100,000 points. Separate schema-1
metadata supplies `measurement_id`, `instrument_id` and the explicit nm air/vacuum
convention with its source and SHA-256; an optional input checksum is checked.
The instrument must match the profile, required bands must be present, and gaps
are not interpolated across. Air wavelengths are explicitly converted to vacuum.

The output preserves input/profile provenance, scores, frozen thresholds and
the profile's refusal decisions. Refused classes and unsupported H/He are unknown.
Its scope is `research_application_not_final_validation`, with calibration limited
to grouped development data. Scores are not concentration estimates or proof of
elemental absence.

## Independent-final schema 3

The new [campaign evaluator](../ml/src/spectralab_ml/evaluation/final_campaign_v3.py)
requires `evaluation_role=independent_final` and freezes metadata-based selection
before predictions. A cross-validation result cannot substitute for that role.
The contract pins the data source, actual model identities and thresholds,
implementation files, instrument, nm/vacuum scoring axis, required wavelength
intervals, native sampling and independently documented optical resolution.
Resolution is not inferred from wavelength spacing.

Fe and Mg each require positive, ordinary-negative and certified method/matrix
blank support. Separate controlled Fe-blank and Mg-blank families are allowed;
one control need not establish both elements. Ordinary low-content negatives do
not become blanks, and blank support does not replace ordinary negatives.
Nominal zeros, missing assays and dark/no-laser records do not establish suitable
blank evidence. Licenses, attributable chemical bounds and source hashes remain
required.

Before scores are read, the plan must specify minimum physical, family, session
and independent-cluster support for each element/group, plus confidence level
and maximum worst-case Wilson half width. There is no invented universal minimum
that automatically establishes adequate precision. Repeated measurements do not
increase independent support. Transitive reviewed aliases and historical IDs/
checksums detect previously seen material, including an already seen converted
vacuum file. Conflicting labels or blank qualification within a physical material
block admission.

Metadata preflight and numerical verification are distinct. An explicitly
requested file check reads one byte buffer, verifies its SHA-256 and parses that
same buffer for shape, row count, finite values, increasing axis, sampling and
complete required coverage. Air-conversion metadata binds original and converted
checksums; the conversion relationship remains an attestation, not a numerically
verified transformation.

Freeze uses exclusive durable creation and opens no spectrum bytes. Final loading
rechecks the freeze, guard code, model bindings and separation between numerical
inputs and artifacts, including path/checksum/hardlink aliases. Evaluation consumes
durable campaign and per-input identity receipts **before** opening numerical
spectra. Renaming a campaign, taking a subset or changing the profile/data source
does not authorize reuse. An interrupted attempt stays consumed. Explicit replay
requires the exact frozen identity and is labelled `locked_replay`, not a new
independent evaluation. All campaigns must share one permanent receipt directory;
deleting it defeats these local guards.

The final assessment is explicitly **`binary_score_threshold_only`** for Fe/Mg.
The CLI uses the profile's scores and thresholds; it does **not** evaluate its
selective/refusal policy. Per-material mean scores yield equal-physical-weight
point errors. Cluster-any-error Wilson intervals are reported separately and
estimate a different quantity; they are not confidence intervals on physical FPR.
Optional frozen comparison profiles share the same verified numerical buffers
and one test consumption. Even a completed software evaluation does not confer
automatic scientific qualification.

## Commands

Run from the repository root after setup. The Makefile exposes these aliases:

```sh
make ml-check
make assay-normalization
make instrument-v3-development
make final-preflight
```

The development command performs a new development run and needs the local
checksummed inputs. Current `make final-preflight` intentionally exits nonzero
because no admissible independent final campaign has been selected.

Equivalent CLI entry points, with explicit paths where useful:

```sh
scripts/uv.sh run --frozen --all-packages --no-sync spectralab ml-check \
  --verify-sources --output reports/ml_environment.json
scripts/uv.sh run --frozen --all-packages --no-sync spectralab normalize-assays \
  --input data/reference/assay_normalization_examples.json \
  --output reports/assay_normalization.json
scripts/uv.sh run --frozen --all-packages --no-sync spectralab study-development \
  --source-plan data/reference/development_v4_source_plan.json \
  --output-root reports/adaptation_v3 --seed 20261019
scripts/uv.sh run --frozen --all-packages --no-sync spectralab final-template \
  --campaign-id new-independent-campaign \
  --output reports/final_campaigns/new-manifest.json
scripts/uv.sh run --frozen --all-packages --no-sync spectralab final-preflight \
  --manifest data/reference/final_campaign_v4_manifest.json \
  --registry data/reference/final_search_v3_admission.json \
  --data-root . --output reports/final_campaigns/preflight.json
```

The template deliberately leaves evidence, policy, profile and samples unfilled;
it refuses to overwrite an existing file. Preflight is metadata-only by default.
`--verify-files` explicitly requests numerical admission checks after metadata
selection; it is not needed to reproduce the current blocked report. `ml-check`
and `study-development` also accept `--root`; preflight and freeze use `--data-root`.

The following are future execution templates. Replace the `reviewed-*.json` paths
with independently reviewed inputs; the current empty campaign cannot be frozen:

```sh
scripts/uv.sh run --frozen --all-packages --no-sync spectralab final-freeze \
  --manifest reviewed-final-manifest.json --registry reviewed-seen-registry.json \
  --data-root . --output reports/final_campaigns/freeze.json
scripts/uv.sh run --frozen --all-packages --no-sync spectralab final-evaluate \
  --freeze reports/final_campaigns/freeze.json --data-root . \
  --receipt-dir reports/final_campaigns/permanent-receipts \
  --output reports/final_campaigns/result.json
scripts/uv.sh run --frozen --all-packages --no-sync spectralab instrument-predict \
  --profile reports/adaptation_v3/development-20261019T172235-da8f0847/profile.json \
  --input measured.csv --metadata measured.metadata.json --input-format csv \
  --output reports/measured_prediction.json
```

`instrument-predict --input-format chemcam-ccs` supports the other documented input
format. `final-evaluate --replay` is only an explicit replay of the identical
previously consumed freeze. Report paths cannot overwrite the protected inputs
or final receipts.

## Current final readiness

[The v4 manifest](../data/reference/final_campaign_v4_manifest.json) is intentionally
empty. Its [saved preflight](final_campaign_v4_preflight.json) is `blocked` with
27 blockers, zero numerical files read and `final_metrics=null`. Missing entries
include an admitted source, frozen profile and support policy, reviewed aliases,
independent samples and certified per-element blanks.

The [source-search v3 findings](final_validation_search_v3.md) remain unresolved.
New chemistry conversion and stronger software guards do not by themselves admit
the existing PDS/CSA candidates. Further work requires independent data satisfying
the declared license, chemistry, identity, axis, coverage, resolution and control
requirements, followed by the frozen one-time assessment. No new final spectra
were opened to manufacture a result for this iteration.


## Verification

The final Python suite passes all 509 tests, including a subprocess CLI integration
of freeze/evaluate/consume/replay using fictional spectra and a constant numeric
model. That fixture deliberately yields FPR=1; it verifies software contracts,
not real scientific quality. The 33 UI unit tests, Ruff (127 files), ESLint,
TypeScript, production build, demo and API smoke pass. A five-seed synthetic
series also completes for NNLS, logistic and random forest after the core fixes.
It uses `demo_fixture`, not independent instrument measurements.

The offline environment check reports 98 passed and no failures. Ten pinned
hashes of historical v2 files/profile and the new development study/profile are
unchanged. No new independent-final intensities were opened, and no source search
or author contact was performed in this iteration. The existing candidate-source
findings retain their dates and unresolved evidence requirements.

See [the verification log](verification.md) and
[the machine-readable verification](ml_v4_verification.json) for commands,
artifact hashes and explicit limits. Raw spectra and trained model artifacts
remain local; tracked source plans and summaries preserve the reproduction inputs.
