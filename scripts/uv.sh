#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export PATH="$ROOT/.tools/bin:$PATH"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.cache/uv}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$ROOT/.tools/python}"
export UV_PYTHON="${UV_PYTHON:-$ROOT/.venv/bin/python}"
exec uv "$@"
