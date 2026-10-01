#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export PATH="$ROOT/.tools/bin:$PATH"
export npm_config_cache="${npm_config_cache:-$ROOT/.cache/npm}"
exec npm "$@"
