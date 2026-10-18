#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
exec backend/.venv/bin/oh-goon-local-dev-launch run --workspace "$ROOT"
