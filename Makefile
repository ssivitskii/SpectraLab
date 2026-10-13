SHELL := /bin/sh
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export MPLBACKEND := Agg
export MPLCONFIGDIR := $(CURDIR)/.cache/matplotlib
export npm_config_cache := $(CURDIR)/.cache/npm
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export OPENBLAS_NUM_THREADS := 1
export OMP_NUM_THREADS := 1
export VECLIB_MAXIMUM_THREADS := 1
export PLAYWRIGHT_BROWSERS_PATH := $(CURDIR)/.cache/ms-playwright
UV := scripts/uv.sh
NPM := scripts/npm.sh
RUN := $(UV) run --frozen --all-packages --no-sync
CONFIG ?= ml/configs/experiments/smoke.yaml
.PHONY: setup demo dev api test lint api-types experiment build analytics smoke series reference worker e2e browser-install open-set external-data external-validation external-validation-download expanded-benchmark
setup:
	sh scripts/setup.sh
demo:
	$(RUN) spectralab demo
	$(RUN) python scripts/smoke.py
api:
	$(RUN) uvicorn app.main:app --host 127.0.0.1 --port 8000
dev:
	$(RUN) python scripts/dev.py
test:
	$(RUN) pytest
	$(NPM) --prefix frontend test
lint:
	$(RUN) ruff check ml backend scripts
	$(RUN) ruff format --check ml backend scripts
	$(NPM) --prefix frontend run lint
	$(NPM) --prefix frontend exec -- tsc -b frontend/tsconfig.json
api-types:
	$(RUN) python scripts/export_openapi.py
	$(NPM) --prefix frontend run api-types
experiment:
	$(RUN) spectralab experiment --config "$(CONFIG)"
series:
	$(RUN) spectralab series --config "$(CONFIG)"
reference:
	$(RUN) python ml/scripts/prepare_reference.py
worker:
	$(RUN) python -m app.worker
browser-install:
	$(NPM) --prefix frontend exec -- playwright install chromium
e2e:
	$(NPM) --prefix frontend exec -- playwright test --config=frontend/playwright.config.ts
open-set:
	$(RUN) python scripts/open_set_benchmark.py
external-data:
	$(RUN) python scripts/external_data.py --download
external-validation:
	$(RUN) python scripts/validate_chemcam.py
external-validation-download:
	$(RUN) python scripts/validate_chemcam.py --download
expanded-benchmark:
	$(RUN) python scripts/expanded_benchmark.py
.PHONY: instrument-data instrument-freeze instrument-adaptation
instrument-data:
	$(RUN) python scripts/prepare_instrument_data.py
instrument-freeze:
	$(RUN) python scripts/adapt_chemcam.py --freeze-only
instrument-adaptation:
	$(RUN) python scripts/adapt_chemcam.py
.PHONY: instrument-v2-data instrument-v2-reference campaign-shift-v2 instrument-v2-development
instrument-v2-data:
	$(RUN) python scripts/prepare_chemcam_v2_intake.py --download-development
instrument-v2-reference:
	$(RUN) python scripts/prepare_uv_reference_v2.py
campaign-shift-v2:
	$(RUN) python scripts/campaign_shift_v2.py --reference data/reference/nist_uv_visible_v2/catalog.json --reference-sha256 504072fb7469e4bd1ae18f07ec2576b56267676dcfb58f8a3fea8ad60790ac6c
instrument-v2-development:
	$(RUN) python scripts/improve_chemcam_v2.py --additional-intake data/reference/chemcam_adaptation_v2_intake.json
build:
	$(NPM) --prefix frontend run build
analytics:
	$(RUN) python scripts/01_reference_data.py
	$(RUN) python scripts/02_synthetic_spectra.py
	$(RUN) python scripts/03_model_comparison.py
smoke:
	$(RUN) python scripts/smoke.py
