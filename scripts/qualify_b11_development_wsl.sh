#!/usr/bin/env bash
# B11: bounded, auditable DEVELOPMENT witness. Never certifies issuer, LACE or release.
# Usage: make qualify-development-wsl [QUALIFY_EVIDENCE_DIR=../...] [QUALIFY_SOURCE_MANIFEST=/trusted/file]
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"
usage() {
  printf '%s\n' 'Usage: bash scripts/qualify_b11_development_wsl.sh [EVIDENCE_DIRECTORY] [EXTERNAL_SOURCE_MANIFEST]' \
    '  Default: ../OH_GOON_B11_NATIVE_EVIDENCE (automatically uses a new numbered path)' \
    '  --snapshot: inspect current source inventory, with no WSL requirement or writes' \
    '  No external manifest is needed for DEVELOPMENT. Changed code is recorded, not trusted.' \
    '  Providing an external manifest opts into strict exact-source checking.'
}
if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then usage; exit 0; fi
if (( $# > 2 )); then usage >&2; exit 64; fi
for command in sha256sum realpath find sort cmp timeout uv seq; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'BLOCKED: missing development qualification tool: %s\n' "$command" >&2
    exit 3
  fi
done
# Use the same generated-directory exclusions on both PRE and POST. Never hash
# dependency or pytest caches; all authored files, both locks included.
source_inventory() {
  local suspect
  suspect="$(find . \( -type d \( -name .git -o -name .venv -o -name node_modules \
      -o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \
      -o -name .mypy_cache -o -name dist -o -name coverage -o -name .oh-goon-data \
      -o -name test-results -o -name release-artifacts \) -prune \) \
      -o \( -type l -o \( ! -type f ! -type d \) \) -print -quit)"
  if [[ -n "$suspect" ]]; then
    printf 'BLOCKED: unsafe source entry: %s\n' "$suspect" >&2
    return 3
  fi
  find . \( -type d \( -name .git -o -name .venv -o -name node_modules \
      -o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \
      -o -name .mypy_cache -o -name dist -o -name coverage -o -name .oh-goon-data \
      -o -name test-results -o -name release-artifacts \) -prune \) \
      -o \( -type f ! -name '*.pyc' ! -name '*.pyo' ! -name '*.tsbuildinfo' \
      ! -name '.coverage*' -print0 \) | LC_ALL=C sort -z | xargs -0 -r sha256sum
}
if [[ "${1:-}" == '--snapshot' ]]; then
  source_inventory
  exit 0
fi
# Do not create evidence or execute project code when the host is not WSL.
if ! grep -Eiq 'microsoft|wsl' /proc/sys/kernel/osrelease /proc/version 2>/dev/null; then
  printf '%s\n' 'BLOCKED: native WSL is required; use normal development tests on other Linux hosts' >&2
  exit 3
fi
if [[ ! -f backend/uv.lock || ! -f frontend/package-lock.json || ! -f backend/pyproject.toml ]]; then
  printf '%s\n' 'BLOCKED: authored backend/frontend dependency locks missing' >&2
  exit 3
fi
base="$(realpath -m -- "${1:-../OH_GOON_B11_NATIVE_EVIDENCE}")"
if [[ "$base" == "$root" || "$base" == "$root/"* || ! -d "$(dirname "$base")" ]]; then
  printf '%s\n' 'BLOCKED: evidence must be in an existing sibling/external directory' >&2
  exit 3
fi
# An explicitly requested external manifest is strict: reject before making any
# evidence directory, never silently fall back to working-tree mode.
external="${2:-}"
if [[ -n "$external" ]]; then
  # An external trusted manifest must cover every authored file, not only a
  # subset whose listed checksums happen to match.
  if [[ -L "$external" || ! -f "$external" ]] \
      || ! sha256sum --status --check "$external" \
      || ! cmp -s "$external" <(source_inventory); then
    printf '%s\n' 'BLOCKED: externally pinned source manifest does not match' >&2
    exit 3
  fi
fi
# No overwrite, no manual cleanup needed. The directory is reserved atomically.
evidence=''
for attempt in $(seq 1 999); do
  if (( attempt == 1 )); then candidate="$base"; else printf -v suffix -- '-run-%03d' "$attempt"; candidate="${base}${suffix}"; fi
  if [[ -L "$candidate" ]]; then
    printf '%s\n' 'BLOCKED: symlinked evidence destination' >&2
    exit 3
  fi
  if mkdir -- "$candidate" 2>/dev/null; then evidence="$candidate"; break; fi
done
if [[ -z "$evidence" ]]; then printf '%s\n' 'BLOCKED: no unused evidence directory' >&2; exit 3; fi
printf 'EVIDENCE_DIR=%s\n' "$evidence"
phase='ADMISSION'
last_result=3
source_before=''
source_after=''
source_integrity='NOT_CHECKED'
# Once admitted, always leave a POST even when pytest, uv, timeout or signals fail.
finish() {
  local exitcode=$?
  trap - EXIT INT TERM
  set +e
  source_inventory > "$evidence/POST_SOURCE.sha256"
  capture_rc=$?
  if (( capture_rc == 0 )) && cmp -s "$evidence/PRE_SOURCE.sha256" "$evidence/POST_SOURCE.sha256"; then
    source_integrity=PASS
  else
    source_integrity=FAIL
    exitcode=3
  fi
  source_before="$(sha256sum "$evidence/PRE_SOURCE.sha256" | cut -d' ' -f1)"
  source_after="$(sha256sum "$evidence/POST_SOURCE.sha256" | cut -d' ' -f1)"
  {
    printf 'schema=oh-goon.development-wsl-witness/v2\n'
    printf 'exit_code=%s\n' "$exitcode"
    printf 'source_integrity=%s\n' "$source_integrity"
    printf 'source_before_sha256=%s\nsource_after_sha256=%s\n' "$source_before" "$source_after"
    printf 'classification=DEVELOPMENT_ONLY\nexternal_issuer=NOT_PROVEN\nphysical_lace=NOT_PROVEN\n'
    printf 'gate_a=BLOCKED\ngate_b=BLOCKED\napp_pass=NOT_CLAIMED\nrelease_authority=NONE\n'
  } > "$evidence/POST.txt"
  sha256sum "$evidence/"*.txt "$evidence/"*.log "$evidence/"*.xml "$evidence/"*.json 2>/dev/null > "$evidence/LOG_SHA256.txt"
  printf 'EVIDENCE_DIR=%s\nRESULT=%s\n' "$evidence" "$exitcode"
  exit "$exitcode"
}
trap finish EXIT
if ! source_inventory > "$evidence/PRE_SOURCE.sha256"; then
  printf '%s\n' 'BLOCKED: source inventory rejected' >&2
  exit 3
fi
# External manifest remains an *opt-in strict* authority. Embedded self-hashes
# are a convenience integrity check only; they cannot authenticate a release.
embedded='.checkpoint/B11_SOURCE.sha256'
binding='WORKING_SOURCE_NOT_EXTERNALLY_ATTESTED'
if [[ -n "$external" ]]; then
  binding='EXTERNAL_MANIFEST_MATCHED_NOT_RELEASE_ATTESTED'
elif [[ -f "$embedded" ]]; then
  # Self-documenting development manifest excludes its own file to avoid
  # circular hashing. Still compare the FULL authored-source inventory.
  if sha256sum --status --check "$embedded" \
      && cmp -s "$embedded" <(source_inventory | grep -v -F '  ./.checkpoint/B11_SOURCE.sha256'); then
    binding='EMBEDDED_MANIFEST_MATCHED_UNTRUSTED_DEVELOPMENT'
  else
    binding='WORKING_SOURCE_DIFFERS_FROM_EMBEDDED_MANIFEST'
    printf '%s\n' 'INFO: working source differs from packaged checkpoint; development tests still allowed' >&2
  fi
fi
{
  printf 'schema=oh-goon.development-wsl-witness/v2\n'
  printf 'classification=DEVELOPMENT_ONLY\n'
  printf 'source_binding=%s\n' "$binding"
  printf 'external_issuer=NOT_PROVEN\nindependent_root_custody=NOT_PROVEN\nphysical_lace=NOT_PROVEN\n'
  printf 'gate_a=BLOCKED\ngate_b=BLOCKED\napp_pass=NOT_CLAIMED\nrelease_authority=NONE\n'
  printf 'wsl_kernel=%s\n' "$(cat /proc/sys/kernel/osrelease)"
  printf 'source_snapshot_sha256=%s\n' "$(sha256sum "$evidence/PRE_SOURCE.sha256" | cut -d' ' -f1)"
} > "$evidence/PRE.txt"
# The ring is supervised by one project-locked Python entrypoint. A pytest
# worker runs in its OWN Linux process group. Even a green JUnit is rejected
# on nonzero exit, missing/invalid XML, deadline, or surviving descendants.
run_ring() {
  local name="$1"; shift
  printf 'RUNNING=%s\n' "$name"
  local rc=0
  (cd backend && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    timeout --signal=TERM --kill-after=10s 210s \
    uv run --locked --group dev python -m oh_goon.repository_tooling.b11_ring_supervisor \
    --name "$name" --evidence "$evidence" --timeout 180 -- "$@") \
    > "$evidence/${name}_LAUNCH.log" 2>&1 || rc=$?
  if [[ ! -s "$evidence/${name}.json" ]]; then
    printf 'BLOCKED: missing authoritative process receipt: %s\n' "$name" \
      >> "$evidence/${name}_LAUNCH.log"
    rc=2
  fi
  printf '%s=%s\n' "$name" "$rc" >> "$evidence/RING_RESULTS.txt"
  if (( rc != 0 )); then last_result=1; fi
}
last_result=0
run_ring CORE \
  tests/unit/runtime/test_b12d2c3b4_authenticated_takeover_preflight.py \
  tests/unit/runtime/test_b12d2c3b5_atomic_restart_transfer.py \
  tests/unit/runtime/test_b12d2c3b7_scheduler_reclaim_fence.py \
  tests/unit/runtime/test_b12d2c3b8_original_issuer_restart_fence.py \
  tests/unit/runtime/test_b12d2c3b9_independent_recovery_root_custody.py \
  tests/unit/services/test_group_a_atomic_fleet_prework_ingress.py \
  tests/unit/services/test_group_a_signer_trust_epoch.py
run_ring B6_PROCESS tests/integration/test_b12d2c3b6_native_restart_witness.py -k 'not interrupted_sqlite_transfer_without_commit'
run_ring B6_ROLLBACK tests/integration/test_b12d2c3b6_native_restart_witness.py -k 'interrupted_sqlite_transfer_without_commit'
run_ring B7_RACES tests/integration/test_b12d2c3b7_scheduler_recovery_races.py
exit "$last_result"
