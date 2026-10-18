#!/usr/bin/env bash
# Real Linux/WSL standard-local qualification. No fake Hashmarks or other production substitutes.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
if [[ "$(uname -s)" != "Linux" ]]; then
  printf 'UNQUALIFIED: requires Linux or WSL\n' >&2
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  printf 'UNQUALIFIED: install uv first (pyproject.toml and uv.lock are authoritative)\n' >&2
  exit 2
fi
printf 'Materializing exactly locked backend runtime (includes pinned Hashmarks) ...\n'
if ! uv sync --locked --project backend --group dev; then
  printf 'UNQUALIFIED: locked dependency sync failed; native Hashmarks and end-to-end execution NOT tested\n' >&2
  exit 2
fi
printf 'Running real standard-local FleetRun / Game Tape / cancellation tests ...\n'
OH_GOON_REQUIRE_NATIVE_HASHMARKS=1 uv run --locked --no-sync --project backend --group dev \
  python -m pytest -q backend/tests/integration/test_exec01b2_native_standard_local.py backend/tests/integration/test_exec01b2b1_owned_process_cleanup.py
printf 'NATIVE STANDARD-LOCAL QUALIFICATION: tests passed on this host\n'
