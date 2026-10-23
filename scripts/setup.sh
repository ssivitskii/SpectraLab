#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
export PATH="$ROOT/.tools/node/bin:$ROOT/.tools/bin:$PATH"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.cache/uv}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$ROOT/.tools/python}"
if ! command -v uv >/dev/null 2>&1; then
  # uv can bootstrap itself from the system Python, then installs a project
  # interpreter matching .python-version during `uv sync`.
  python3 -m venv .tools/bootstrap
  .tools/bootstrap/bin/python -m pip install uv==0.6.17
  mkdir -p .tools/bin
  cp .tools/bootstrap/bin/uv .tools/bin/uv
fi
sh "$ROOT/scripts/setup_node.sh"
uv sync --frozen --all-packages
npm --prefix frontend ci --cache "$ROOT/.cache/npm" --no-audit --no-fund
