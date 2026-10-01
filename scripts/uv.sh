#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export PATH="$ROOT/.tools/bin:$PATH"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.cache/uv}"
exec uv "$@"
