#!/usr/bin/env bash
set -Eeuo pipefail
release_action="${1:-build}"
if [[ "$#" -gt 1 || ( "$release_action" != build && "$release_action" != assemble ) ]]; then
  echo "usage: build_release.sh [build|assemble]" >&2
  exit 2
fi
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

if [[ "$release_action" == build ]]; then
  make generate
  make release-check
fi
make frontend-build

clear_release_outputs
mkdir -p "$ROOT/generated/release/static" "$ROOT/generated/release/dist"
cp -a frontend/dist/. generated/release/static/
require_built_ui
uv run --project backend --offline --frozen --no-sync oh-goon-release-wheel-build
