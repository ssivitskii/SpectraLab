SHELL := /bin/sh
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export MPLBACKEND := Agg
export MPLCONFIGDIR := $(CURDIR)/.cache/matplotlib
export npm_config_cache := $(CURDIR)/.cache/npm
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export OPENBLAS_NUM_THREADS := 1
export OMP_NUM_THREADS := 1
export VECLIB_MAXIMUM_THREADS := 1
UV := scripts/uv.sh
NPM := scripts/npm.sh
RUN := $(UV) run --frozen --all-packages --no-sync
CONFIG ?= ml/configs/experiments/smoke.yaml
.PHONY: setup demo dev api test lint api-types experiment build analytics smoke
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
build:
	$(NPM) --prefix frontend run build
analytics:
	$(RUN) python scripts/01_reference_data.py
	$(RUN) python scripts/02_synthetic_spectra.py
	$(RUN) python scripts/03_model_comparison.py
smoke:
	$(RUN) python scripts/smoke.py
