---
version: 1
slug: "frontend-src-app-tsx"
primary_target: "frontend/src/App.tsx"
related_targets: ["frontend/src/pages/Analysis.tsx","frontend/src/pages/Generator.tsx","frontend/src/pages/Experiments.tsx","frontend/src/pages/BatchAnalysis.tsx","frontend/src/components/Chart.tsx","frontend/src/pages/Measured.tsx","frontend/src/pages/SystemStatus.tsx"]
---

# SpectraLab workspace

Scope: replace the visual world across Analysis, Generator, Experiments and Batch Analysis. Mode: Operate. User explicitly selected Photon and all four sections on 2026-10-20.

## Direction contract

THESIS: The spectrum is the shared reference between an input and model-specific element decisions. No vanity KPI dashboard or simulated hardware.

OWN-WORLD: Flat graphite surfaces, cool white text, cyan observed signal and primary action, mint/amber/red only for meaningful states. Legible Cyrillic sans, tabular figures, consistent outline icons, precise 12px panel corners.

STORY: Choose or generate a spectrum, inspect it, explicitly run models, then compare decisions with their thresholds and provenance. The same grammar carries experiments and persistent queues.

FIRST VIEWPORT: A labelled 218px navigation rail; compact context bar; clear title; wide spectrum/result workspace and a 278px input/model inspector on desktop. Generator and queues retain task-first controls. Phone navigation folds behind a labelled menu, with input before analysis output.

FORM: Particle-physics publication graphics, candidate 4, seed 3a210325. Approved code-led sketch: .impeccable/mocks/decision/photon.html. Signature interaction: a reference line and its table row highlight together while zoom remains stable. Standard web controls remain familiar.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Constraints

Preserve four routes, deep links, existing API and race/queue/export behaviour.
No fake online worker, live instrument, concentration or accuracy claims.
Scores retain their model-specific meaning. Empty states show the real state.
Review desktop 1440, current browser width 1280, tablet 768 and phone 390.

## Finish evidence · 2026-10-20

All four routes were checked at desktop and phone widths, with twelve valid
review captures. The independent finish reviewer returned `fix`; one combined
batch resolved all seven findings, and its verdict pass returned `ship` for
those scored fixes. A generic fresh agent substituted for the unavailable
dedicated reviewer type. The one mechanical detector pass returned `[]`.

The actual system is recorded in root `DESIGN.md` and `.impeccable/design.json`.
Verification scope and the tracked application capture are in `docs/photon.md`.
There are no shipping product rasters; the UI uses SVG and runtime data plots.

## Extension · 2026-10-23

Preserve the selected Photon world while integrating origin/main colleague work.
The user explicitly requests both new sections: Measured and System Status.
All six routes keep deep links and real API state. Measured places instrument,
CSV and axis provenance before saved results; System Status reports actual
worker lock/heartbeat, queue, disk/quota and backup records, never fake telemetry.
Both collapse into a single column on phones. Existing direction contract,
seed and tokens remain authoritative. This is a code-led extension, with no
new concept roll or comp round. Scientific final validation stays incomplete.

### Extension verification

The extension reuses the incumbent Photon tokens, local Golos Text, outline SVG,
flat panels, focus treatment and responsive shell. The measured table keeps
numeric values right-aligned and unbroken; compact status labels remain whole
inside its horizontal scroll region. No product raster was added. The mirrored
system guardrail now prohibits a worker heartbeat claim unsupported by the API;
actual heartbeat provenance is part of the System Status API.

The finish review received 18 valid captures across all six routes at 1440 and
390, plus the new routes at 1280 and 768 and the measured result state. Its one
scored table finding was corrected in one batch; the fresh verdict was
"Clear. Ship covers the scored table-readability fix." Generic fresh agents
substituted for unavailable dedicated reviewer/documenter types. No separate
QUALITY BAR card was supplied; the agreed direction contract and incumbent
`DESIGN.md` provided the visual authority. Local verification and scientific
limits are recorded in `docs/photon_integration.md`; the specific CI result
must be verified after push and is not implied by the local pass.
