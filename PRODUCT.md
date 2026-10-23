# SpectraLab

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One researcher working on one local computer. Confirmed in this conversation;
Photon now covers all six application sections, including measured spectra and system status.

## Product Purpose

Explore atomic emission spectra, generate synthetic samples, compare estimates
of elemental presence and run reproducible model experiments.

## Operating Context

React 19 / TypeScript / Vite frontend, Plotly charts and FastAPI backend.
Russian interface. Six routes: analysis, measured spectra, generator, experiments,
batch analysis and system status. A separate local worker executes queued jobs.

## Capabilities and Constraints

Preserve upload validation, units and wavelength medium, stored spectra,
model selection, score/threshold tables, chart interaction, reference-line
inspection, experiments, batch jobs, exports and deletion safeguards.
Preserve existing API contracts and browser navigation/deep links.

This is a research product. Scores are not automatically probabilities;
synthetic mixture weights are not measured concentrations. Reference lines
are diagnostic associations. No live spectrometer, team accounts or remote
multiuser service is implemented. Worker state comes from the actual host lock and heartbeat, not capabilities.
The trusted Ca v4 research profile is available through the measured API as well
as CLI, with explicit instrument and calibrated axis provenance. The LTE plasma
generator remains a separate research script, not the measured API model.
Independent final validation remains incomplete.

## Brand Commitments

Keep the SpectraLab name and Russian scientific terminology. The user asks for
the Photon visual world selected on 2026-10-20, extended to colleagues’ new
sections using UI/UX Pro Max and Impeccable. Preserve its existing tokens,
self-hosted type, outline icons and task-first layouts.

## Evidence on Hand

Current frontend, API client and tests; README; experiment and validation
documentation. Use real local API results and clearly labelled synthetic
demonstrations. Do not invent model accuracy, chemical concentrations,
worker online status, measurement provenance or independent validation.

## Product Principles

- Keep the spectrum and the research task central.
- Distinguish inputs, predictions and chemical ground truth.
- Make pending, empty, failed and completed states understandable.
- Keep all six research workflows usable by keyboard and on a phone.
- Preserve scientific limitations while moving detailed explanation out of the task's primary path.
