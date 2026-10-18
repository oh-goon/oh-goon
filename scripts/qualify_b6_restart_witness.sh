#!/usr/bin/env bash
# B6: OPTIONAL read-only/native-process *development witness*, NOT APP or Fleet admission.
# Invoke only against a fresh, extracted checkpoint on native WSL2.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ $# -ne 1 || -z "$1" ]]; then
  printf '%s\n' 'usage: bash scripts/qualify_b6_restart_witness.sh ../OH_GOON_B6_NATIVE_EVIDENCE' >&2
  exit 64
fi
if [[ ! -f /proc/sys/kernel/osrelease ]] || ! grep -Eqi 'microsoft|wsl' /proc/sys/kernel/osrelease; then
  printf '%s\n' 'BLOCKED: native WSL kernel not proven (no Linux-container fallback)' >&2
  exit 3
fi
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'BLOCKED: uv required for native locked Python execution' >&2
  exit 3
fi
if [[ ! -f backend/uv.lock || ! -f backend/pyproject.toml ]]; then
  printf '%s\n' 'BLOCKED: authoritative backend lock / project absent' >&2
  exit 3
fi
# Never overwrite or conflate a previous qualification's provenance.
evidence=$1
if [[ -e "$evidence" ]]; then
  printf '%s\n' 'BLOCKED: evidence target already exists' >&2
  exit 3
fi
mkdir -p -- "$evidence"
evidence="$(cd "$evidence" && pwd -P)"
{
  printf '%s\n' 'schema=oh-goon.original-restart-native-witness/v1'
  printf '%s\n' 'classification=DEVELOPMENT_WITNESS_ONLY'
  printf '%s\n' 'original_provisioned_signer=NOT_PROVEN'
  printf '%s\n' 'physical_native_lace_cleanup=NOT_EXECUTED'
  printf '%s\n' 'gate_a=BLOCKED'
  printf '%s\n' 'gate_b=BLOCKED'
  printf '%s\n' 'app_pass=NOT_CLAIMED'
  printf '%s\n' 'wsl_kernel='"$(cat /proc/sys/kernel/osrelease)"
  printf '%s\n' 'backend_uv_lock_sha256='"$(sha256sum backend/uv.lock | cut -d' ' -f1)"
  printf '%s\n' 'parent_source=OH_GOON_B5_CANDIDATE'
} > "$evidence/PRE.txt"

set +e
(
  cd backend
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --locked --group dev pytest -q \
    tests/integration/test_b12d2c3b6_native_restart_witness.py \
    tests/unit/runtime/test_b12d2c3b5_atomic_restart_transfer.py \
    --junitxml="$evidence/JUNIT.xml"
) > "$evidence/TEST.log" 2>&1
rc=$?
set -e
lock_after="$(sha256sum backend/uv.lock | cut -d' ' -f1)"
lock_before="$(grep '^backend_uv_lock_sha256=' "$evidence/PRE.txt" | cut -d= -f2)"
if [[ "$lock_before" != "$lock_after" ]]; then
  printf '%s\n' 'BLOCKED: authoritative backend lock changed during witness' >&2
  rc=3
fi
{
  printf 'exit_code=%s\n' "$rc"
  printf 'backend_uv_lock_sha256_after=%s\n' "$lock_after"
  printf '%s\n' 'classification=DEVELOPMENT_WITNESS_ONLY'
  printf '%s\n' 'release_authority=NONE'
} > "$evidence/POST.txt"
sha256sum "$evidence/PRE.txt" "$evidence/POST.txt" "$evidence/TEST.log" > "$evidence/LOG_SHA256.txt"
exit "$rc"
