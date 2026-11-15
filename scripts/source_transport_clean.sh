#!/usr/bin/env bash
# Pre-environment frontdoor: the packaged native owner needs no Python or network.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ ! -f "$root/backend/pyproject.toml" || ! -f "$root/VERSION" ]]; then
  echo 'SOURCE TRANSPORT CLEAN BLOCKED: not an OH-goon source root' >&2
  exit 2
fi
exec bash "$root/backend/src/oh_goon/repository_tooling/source_transport_clean.sh" "$root"
