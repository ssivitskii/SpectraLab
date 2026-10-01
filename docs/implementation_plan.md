# SpectraLab implementation plan

## Outcome and constraints

Deliver an offline-first research MVP with one real vertical path: a configured neutral-atom
line reference produces a synthetic spectrum, the backend stores and classifies it through
`spectralab_ml`, and the Russian React UI displays the measured signal and honest model
scores. The fixture is always identified as `demo_fixture`; wavelengths are vacuum nm;
component weights are not concentrations; training never runs in an HTTP request.

## Dependency DAG

1. Workspace manifests and shared configuration.
2. Reference importer and demo fixture.
3. Pixel-integrated simulator and shared preprocessing.
4. Baseline, trainable models, manifests, evaluation, and experiments.
5. File-backed API and OpenAPI schema.
6. Typed React dashboard over the live API.
7. Deterministic unit, API, UI, and smoke tests; documentation and CI.

Steps 2–4 depend on step 1. Step 5 depends on steps 2–4. Step 6 depends on step 5.
Tests are added with their owning layer and the final smoke test crosses steps 2–6.

## Acceptance commands

```bash
make setup
make demo
make test
make lint
make api-types
npm --prefix frontend run build
make experiment CONFIG=ml/configs/experiments/smoke.yaml
```

Runtime smoke: start `make api`, check `/api/v1/health`, generate a spectrum, call
`/api/v1/predict`, and load all three UI routes in a browser.
