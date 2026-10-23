#!/bin/sh
# Install the pinned Node/npm pair locally when the host runtime does not match.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export PATH="$ROOT/.tools/node/bin:$ROOT/.tools/bin:$PATH"
NODE_VERSION=$(tr -d '[:space:]' < "$ROOT/.nvmrc")
NPM_VERSION=10.9.2
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 &&
  [ "$(node --version)" = "v$NODE_VERSION" ] && [ "$(npm --version)" = "$NPM_VERSION" ]; then
  exit 0
fi
if [ -e "$ROOT/.tools/node" ]; then
  echo "Existing .tools/node does not match Node $NODE_VERSION / npm $NPM_VERSION." >&2
  echo "Move it aside and rerun make setup, or activate the required system runtime." >&2
  exit 1
fi
case "$(uname -s)" in
  Darwin) NODE_OS=darwin ;;
  Linux) NODE_OS=linux ;;
  *) echo "Automatic Node setup supports macOS/Linux; install Node $NODE_VERSION manually." >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64|aarch64) NODE_ARCH=arm64 ;;
  x86_64|amd64) NODE_ARCH=x64 ;;
  *) echo "Unsupported Node architecture; install Node $NODE_VERSION manually." >&2; exit 1 ;;
esac
mkdir -p "$ROOT/.cache" "$ROOT/.tools"
NODE_STAGE=$(mktemp -d "$ROOT/.cache/node-setup.XXXXXX")
NODE_ARCHIVE="node-v$NODE_VERSION-$NODE_OS-$NODE_ARCH.tar.gz"
NODE_BASE="https://nodejs.org/dist/v$NODE_VERSION"
echo "Installing Node $NODE_VERSION / npm $NPM_VERSION in .tools/node"
curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
  --connect-timeout 15 --max-time 180 --retry 2 \
  "$NODE_BASE/$NODE_ARCHIVE" --output "$NODE_STAGE/archive.tar.gz"
curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
  --connect-timeout 15 --max-time 60 --retry 2 \
  "$NODE_BASE/SHASUMS256.txt" --output "$NODE_STAGE/SHASUMS256.txt"
NODE_EXPECTED=$(awk -v name="$NODE_ARCHIVE" '$2 == name {print $1}' "$NODE_STAGE/SHASUMS256.txt")
if command -v shasum >/dev/null 2>&1; then
  NODE_ACTUAL=$(shasum -a 256 "$NODE_STAGE/archive.tar.gz" | awk '{print $1}')
else
  NODE_ACTUAL=$(sha256sum "$NODE_STAGE/archive.tar.gz" | awk '{print $1}')
fi
if [ -z "$NODE_EXPECTED" ] || [ "$NODE_EXPECTED" != "$NODE_ACTUAL" ]; then
  echo "Node archive checksum mismatch; retained downloads in $NODE_STAGE" >&2
  exit 1
fi
mkdir "$NODE_STAGE/runtime"
tar -xzf "$NODE_STAGE/archive.tar.gz" --strip-components=1 -C "$NODE_STAGE/runtime"
mv "$NODE_STAGE/runtime" "$ROOT/.tools/node"
node --version
npm --version
