# SpectraLab

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One researcher working on one local computer. Confirmed in this conversation;
the current redesign covers all four application sections.

## Product Purpose

Explore atomic emission spectra, generate synthetic samples, compare estimates
of elemental presence and run reproducible model experiments.

## Operating Context

React 19 / TypeScript / Vite frontend, Plotly charts and FastAPI backend.
Russian interface. Four existing routes: analysis, generator, experiments,
and batch analysis. A separate local worker executes queued jobs.

## Capabilities and Constraints

Preserve upload validation, units and wavelength medium, stored spectra,
model selection, score/threshold tables, chart interaction, reference-line
inspection, experiments, batch jobs, exports and deletion safeguards.
Preserve existing API contracts and browser navigation/deep links.

This is a research product. Scores are not automatically probabilities;
synthetic mixture weights are not measured concentrations. Reference lines
are diagnostic associations. No live spectrometer, team accounts or remote
multiuser service is implemented. Worker capability is not worker heartbeat.
The published Ca v4 research profile is a CLI artifact, not an API model.
Independent final validation remains incomplete.

## Brand Commitments

Keep the SpectraLab name and Russian scientific terminology. The user asks for
a new visual design from scratch, maximally technological, using UI/UX Pro Max,
Taste and Impeccable. The old visual styling has no authority in this redesign.

## Evidence on Hand

Current frontend, API client and tests; README; experiment and validation
documentation. Use real local API results and clearly labelled synthetic
demonstrations. Do not invent model accuracy, chemical concentrations,
worker online status, measurement provenance or independent validation.

## Product Principles

- Keep the spectrum and the research task central.
- Distinguish inputs, predictions and chemical ground truth.
- Make pending, empty, failed and completed states understandable.
- Keep all four research workflows usable by keyboard and on a phone.
- Preserve scientific limitations while moving detailed explanation out of the task's primary path.
