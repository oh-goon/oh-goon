#!/usr/bin/env bash
# B9: DEVELOPMENT ONLY. Independent OS public-root custody is mandatory for recovery, but not externally attested by this script.
# Require exact checkpoint manifest outside source tree; never invent local keys.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ $# -ne 2 || -z "$1" || -z "$2" ]]; then
  printf '%s\n' 'usage: bash scripts/qualify_b9_independent_custody.sh ../OH_GOON_B9_NATIVE_EVIDENCE ../B12D2C3B9_MANIFEST.sha256' >&2
  exit 64
fi
if [[ ! -f /proc/sys/kernel/osrelease ]] || ! grep -Eqi 'microsoft|wsl' /proc/sys/kernel/osrelease; then
  printf '%s\n' 'BLOCKED: original native WSL2 kernel not positively observed' >&2
  exit 3
fi
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'BLOCKED: uv required for locked native execution' >&2
  exit 3
fi
if ! command -v sha256sum >/dev/null 2>&1 || [[ ! -f backend/uv.lock || ! -f backend/pyproject.toml ]]; then
  printf '%s\n' 'BLOCKED: native digest or authoritative Python lock missing' >&2
  exit 3
fi
# Neither a source-tree evidence destination nor a symlinked manifest may be
# accepted: both could mingle attempted proof with source/earlier evidence.
evidence_target=$(realpath -m -- "$1")
source_root=$(pwd -P)
if [[ "$evidence_target" == "$source_root" || "$evidence_target" == "$source_root/"* \
      || ! -d "$(dirname -- "$evidence_target")" \
      || -e "$evidence_target" || -L "$evidence_target" || -L "$2" || ! -f "$2" ]]; then
  printf '%s\n' 'BLOCKED: evidence path exists or exact source manifest missing' >&2
  exit 3
fi
manifest=$(realpath -e -- "$2")
# Symlinks can replace a regular-source member but stay invisible to sha256sum
# and a regular-file count. Reject such entries before executing project code.
if [[ -n "$(find . -path './backend/.venv' -prune -o -path './frontend/node_modules' -prune -o -type l -print -quit)" ]]; then
  printf '%s\n' 'BLOCKED: symlinked source entry is not an authoritative archive member' >&2
  exit 3
fi
# Fail before creating any evidence or running project code on different sources.
if ! sha256sum --status --check "$manifest"; then
  printf '%s\n' 'BLOCKED: extracted files differ from authoritative checkpoint manifest' >&2
  exit 3
fi
source_members=$(wc -l < "$manifest")
# This witness is intended for a FRESH extraction, not an untracked altered tree.
local_members=$(find . -type f -not -path './backend/.venv/*' | wc -l)
if [[ "$source_members" != "$local_members" ]]; then
  printf '%s\n' 'BLOCKED: extra/missing source members in extraction' >&2
  exit 3
fi
lock_before=$(sha256sum backend/uv.lock | cut -d' ' -f1)
mkdir -- "$evidence_target"
evidence="$evidence_target"
{
  printf '%s\n' 'schema=oh-goon.original-independent-root-custody-wsl/v1'
  printf '%s\n' 'classification=NATIVE_WSL_DEVELOPMENT_WITNESS_ONLY'
  printf '%s\n' 'original_external_fleet_issuer=NOT_PROVEN'
  printf '%s\n' 'original_issuer_fingerprint=UNAVAILABLE_NO_SIGNER_PROVISIONED'
  printf '%s\n' 'independent_external_root_ownership=NOT_PROVEN_BY_DEVELOPMENT_TESTS'
  printf '%s\n' 'physical_lace_rediscovery=NOT_EXECUTED'
  printf '%s\n' 'gate_a=BLOCKED'
  printf '%s\n' 'gate_b=BLOCKED'
  printf '%s\n' 'app_pass=NOT_CLAIMED'
  printf '%s\n' 'replay_authority=NONE'
  printf 'wsl_kernel=%s\n' "$(cat /proc/sys/kernel/osrelease)"
  printf 'source_manifest_sha256=%s\n' "$(sha256sum "$manifest" | cut -d' ' -f1)"
  printf 'source_members=%s\n' "$source_members"
  printf 'backend_uv_lock_sha256=%s\n' "$lock_before"
} > "$evidence/PRE.txt"
set +e
(
  cd backend
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --locked --group dev pytest -q \
    tests/unit/runtime/test_b12d2c3b4_authenticated_takeover_preflight.py \
    tests/unit/runtime/test_b12d2c3b5_atomic_restart_transfer.py \
    tests/integration/test_b12d2c3b6_native_restart_witness.py \
    tests/unit/runtime/test_b12d2c3b7_scheduler_reclaim_fence.py \
    tests/integration/test_b12d2c3b7_scheduler_recovery_races.py \
    tests/unit/runtime/test_b12d2c3b8_original_issuer_restart_fence.py \
    tests/unit/runtime/test_b12d2c3b9_independent_recovery_root_custody.py \
    tests/unit/services/test_a1f_independent_os_root_custody.py \
    tests/unit/services/test_group_a_atomic_fleet_prework_ingress.py \
    tests/unit/services/test_group_a_signer_trust_epoch.py \
    --junitxml="$evidence/JUNIT.xml"
) > "$evidence/TEST.log" 2>&1
rc=$?
set -e
lock_after=$(sha256sum backend/uv.lock | cut -d' ' -f1)
if [[ "$lock_before" != "$lock_after" ]] || ! sha256sum --status --check "$manifest"; then
  printf '%s\n' 'BLOCKED: native locked source changed during qualification' >&2
  rc=3
fi
{
  printf 'exit_code=%s\n' "$rc"
  printf 'backend_uv_lock_sha256_after=%s\n' "$lock_after"
  printf '%s\n' 'classification=DEVELOPMENT_ONLY'
  printf '%s\n' 'release_authority=NONE'
} > "$evidence/POST.txt"
sha256sum "$evidence/PRE.txt" "$evidence/POST.txt" "$evidence/TEST.log" > "$evidence/LOG_SHA256.txt"
exit "$rc"
