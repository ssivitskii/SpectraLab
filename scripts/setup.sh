#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
export PATH="$ROOT/.tools/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  python3.12 -m venv .tools/bootstrap
  .tools/bootstrap/bin/python -m pip install uv==0.6.17
  mkdir -p .tools/bin
  cp .tools/bootstrap/bin/uv .tools/bin/uv
fi
node -e 'if(process.versions.node!=="22.14.0") { console.error("Use Node 22.14.0: nvm install && nvm use"); process.exit(1) }'
uv sync --frozen --all-packages
npm --prefix frontend ci --cache "$ROOT/.cache/npm" --no-audit --no-fund
