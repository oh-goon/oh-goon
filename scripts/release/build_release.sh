#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"
export SOURCE_DATE_EPOCH=1580601600

clear_release_outputs() {
  rm -rf "$ROOT/generated/release/static" "$ROOT/generated/release/dist"
}

cleanup_failed_release() {
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    clear_release_outputs
  fi
}
trap cleanup_failed_release EXIT

require_built_ui() {
  [[ -f "$ROOT/generated/release/static/index.html" ]] && return 0
  echo "Built UI index.html is missing" >&2
  return 1
}

# One pre-materialization dependency-admissibility owner. This target delegates
# lock semantics to native uv/npm exactly once for this release-build journey.
make release-locks-check
clear_release_outputs

cd backend && uv sync --locked --group dev --group quality --group build --no-python-downloads
cd "$ROOT"
backend/.venv/bin/oh-goon-release-version check
make frontend-install

make generate
make check
make frontend-build

clear_release_outputs
mkdir -p "$ROOT/generated/release/static" "$ROOT/generated/release/dist"
cp -a frontend/dist/. generated/release/static/
require_built_ui
(cd backend && uv build --wheel --no-build-isolation --no-sources --no-python-downloads --out-dir ../generated/release/dist)
rm -f "$ROOT/generated/release/dist/.gitignore"
backend/.venv/bin/oh-goon-release-artifact-check
