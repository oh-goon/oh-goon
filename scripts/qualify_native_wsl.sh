#!/usr/bin/env bash
# M209: optional native WSL qualification helper; not an APP or release authority.
# One attempt, one evidence directory, command-ordered receipts, and a post-run snapshot.
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if (( $# != 1 )) || [[ -z "$1" ]]; then
  printf 'Usage: bash scripts/qualify_native_wsl.sh EVIDENCE_DIRECTORY\n' >&2
  exit 64
fi
if ! grep -Eiq '(microsoft|wsl)' /proc/sys/kernel/osrelease /proc/version 2>/dev/null; then
  printf 'NATIVE WSL QUALIFICATION BLOCKED: WSL kernel required; no commands executed\n' >&2
  exit 3
fi
for executable in make sha256sum find sort tee cmp realpath date node; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    printf 'NATIVE WSL QUALIFICATION BLOCKED: missing required tool %s\n' "$executable" >&2
    exit 3
  fi
done
for relative in Makefile backend/uv.lock frontend/package-lock.json scripts/qualify_native_issuer_wheel.sh; do
  if [[ ! -f "$root/$relative" || -L "$root/$relative" ]]; then
    printf 'NATIVE WSL QUALIFICATION BLOCKED: missing or symlinked %s\n' "$relative" >&2
    exit 3
  fi
done
# Refuse an existing evidence directory rather than silently mixing attempts.
evidence="$(realpath -m -- "$1")"
if [[ "$evidence" == "$root" || "$evidence" == "$root/"* || -e "$evidence" || -L "$evidence" || ! -d "$(dirname "$evidence")" ]]; then
  printf 'NATIVE WSL QUALIFICATION BLOCKED: evidence must be a new sibling/external directory: %s\n' "$evidence" >&2
  exit 3
fi
cd "$root"
mkdir -- "$evidence"
steps="$evidence/M209_STEPS.tsv"
printf 'step\texit_code\tclassification\n' > "$steps"
phase='ADMISSION'
last_step='NONE'
last_exit='NOT_RUN'
certification='NOT_RUN'
source_pre='NOT_CAPTURED'
source_post='NOT_CAPTURED'
source_compare='NOT_EVALUATED'
pre_backend='NOT_CAPTURED'
pre_frontend='NOT_CAPTURED'
post_backend='NOT_CAPTURED'
post_frontend='NOT_CAPTURED'
lock_compare='NOT_EVALUATED'

# M210 regression: the source transport cleanup owner deliberately removes Windows
# MOTW sidecars and patch transport files. Snapshot only authored/source-like files
# before and after that owner, so permitted cleanup is not called SOURCE_DRIFT.
# Match only the four exact suffixes from source_transport_metadata.py; tests
# enforce parity with the Python owner. Other unexpected changes still fail.
# Dependency materializations are outside the snapshot; both authoritative locks
# and generated contracts are included. Evidence is outside the source root.
is_transport_name() {
  local name="$1"
  case "$name" in
    *:Zone.Identifier) [[ "$name" != ':Zone.Identifier' ]] ;;
    *:Zone.Identifier:\$DATA) [[ "$name" != ':Zone.Identifier:$DATA' ]] ;;
    *.patch.sha256) [[ "$name" != '.patch.sha256' ]] ;;
    *.patch) [[ "$name" != '.patch' ]] ;;
    *) return 1 ;;
  esac
}

source_members() {
  (
    cd "$root"
    # A1d: the hashed regular-file inventory is only complete when the
    # candidate has no untracked symlinks or special filesystem objects.
    # Such entries were previously invisible to both PRE and POST snapshots.
    # Prune precisely the same generated/dependency directories as below.
    local unsafe_entry
    unsafe_entry="$(find . \( -type d \( \
        -name .git -o -name .venv -o -name node_modules -o -name dist \
        -o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \
        -o -name .mypy_cache -o -name .oh-goon-playwright-browsers \
        -o -name .oh-goon-vite-cache -o -name .oh-goon-data \
        -o -name .hashmarks -o -name coverage -o -name playwright-report \
        -o -name playwright-runtime-report -o -name test-results -o -name release-artifacts \
      \) -prune \) -o \( -type l -o \( ! -type f ! -type d \) \) -print -quit)" || return 1
    if [[ -n "$unsafe_entry" ]]; then
      printf 'NATIVE WSL QUALIFICATION BLOCKED: untracked symlink or special source entry\n' >&2
      return 1
    fi
    find . \( -type d \( \
        -name .git -o -name .venv -o -name node_modules -o -name dist \
        -o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \
        -o -name .mypy_cache -o -name .oh-goon-playwright-browsers \
        -o -name .oh-goon-vite-cache -o -name .oh-goon-data \
        -o -name .hashmarks -o -name coverage -o -name playwright-report \
        -o -name playwright-runtime-report -o -name test-results -o -name release-artifacts \
      \) -prune \) -o \( -type f ! -name '*.pyc' ! -name '*.pyo' \
          ! -name '*.tsbuildinfo' ! -name '.coverage*' ! -name coverage-python.json \
          -print0 \) | \
      while IFS= read -r -d '' path; do
        if ! is_transport_name "${path##*/}"; then
          printf '%s\0' "$path"
        fi
      done | LC_ALL=C sort -z | xargs -0 -r sha256sum
  )
}

# Always finish an *admitted* attempt with the POST text, including interrupted
# and failed attempts. Errors collecting proof are not converted into success.
finish() {
  local rc=$?
  trap - EXIT INT TERM
  set +e
  phase="${phase:-ADMISSION}"
  if [[ -f "$evidence/M209_PRE_RUN_SOURCE.sha256" ]]; then
    source_pre="$(sha256sum "$evidence/M209_PRE_RUN_SOURCE.sha256" | cut -d' ' -f1)"
  fi
  if source_members > "$evidence/M209_POST_RUN_SOURCE.sha256"; then
    source_post="$(sha256sum "$evidence/M209_POST_RUN_SOURCE.sha256" | cut -d' ' -f1)"
    if [[ -f "$evidence/M209_PRE_RUN_SOURCE.sha256" ]]; then
      if cmp -s "$evidence/M209_PRE_RUN_SOURCE.sha256" "$evidence/M209_POST_RUN_SOURCE.sha256"; then
        source_compare=PASS
      else
        source_compare=FAIL
      fi
    fi
  else
    source_post=COLLECTOR_FAILED
    source_compare=UNKNOWN
  fi
  post_backend="$(sha256sum backend/uv.lock 2>/dev/null | cut -d' ' -f1)"
  post_frontend="$(sha256sum frontend/package-lock.json 2>/dev/null | cut -d' ' -f1)"
  if [[ -n "$pre_backend" && "$pre_backend" == "$post_backend" && "$pre_frontend" == "$post_frontend" ]]; then
    lock_compare=PASS
  else
    lock_compare=FAIL
  fi
  # A1e: an exact manifest of every attempted owner's raw log. File presence alone
  # was not enough: a reviewer could previously accept arbitrarily edited logs.
  # This is diagnostic integrity, NEVER an attestation of the executing host.
  local logs_manifest="$evidence/M209_LOGS.sha256"
  local logs_status=PASS log_name
  : > "$logs_manifest"
  for log_name in "$evidence"/*.log; do
    [[ -e "$log_name" || -L "$log_name" ]] || continue
    if [[ ! -f "$log_name" || -L "$log_name" ]]; then
      logs_status=FAIL
      continue
    fi
    (cd "$evidence" && sha256sum -- "${log_name##*/}") >> "$logs_manifest" || logs_status=FAIL
  done
  local logs_manifest_sha
  logs_manifest_sha="$(sha256sum "$logs_manifest" | cut -d' ' -f1)"
  local post="$evidence/M209_POST_RUN_PROVENANCE.txt"
  {
    printf 'schema=oh-goon.native-wsl-qualification-provenance/v2\n'
    printf 'note=SAME_ATTEMPT_BEFORE_AND_AFTER_SNAPSHOTS_NOT_APP_CERTIFICATION\n'
    printf 'captured_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'source_root=%s\n' "$root"
    printf 'kernel=%s\n' "$(uname -r)"
    printf 'wsl_detected=true\n'
    printf 'uv_version=%s\n' "$(uv --version 2>/dev/null || printf 'unavailable')"
    printf 'node_version=%s\n' "$(node --version 2>/dev/null || printf 'unavailable')"
    printf 'npm_version=%s\n' "$(npm --version 2>/dev/null || printf 'unavailable')"
    printf 'pre_run_source_manifest_sha256=%s\n' "$source_pre"
    printf 'post_run_source_manifest_sha256=%s\n' "$source_post"
    printf 'canonical_members_integrity_same_attempt=%s\n' "$source_compare"
    printf 'backend_uv_lock_before=%s\n' "$pre_backend"
    printf 'backend_uv_lock_after=%s\n' "$post_backend"
    printf 'frontend_npm_lock_before=%s\n' "$pre_frontend"
    printf 'frontend_npm_lock_after=%s\n' "$post_frontend"
    printf 'locks_byte_identical_same_attempt=%s\n' "$lock_compare"
    printf 'last_step=%s\nlast_step_exit=%s\n' "$last_step" "$last_exit"
    printf 'qualification_phase=%s\nrunner_exit=%s\n' "$phase" "$rc"
    printf 'certify_classification=%s\n' "$certification"
    printf 'app_pass=NOT_ESTABLISHED\nauthenticated_replay=NOT_AUTHORIZED\ngate_a=NOT_RUN\nphysical_lace=NOT_ESTABLISHED\nrelease=BLOCKED\n'
    printf 'command_exit_receipts=%s\n' "$(basename "$steps")"
    printf 'command_exit_receipts_sha256=%s\n' "$(sha256sum "$steps" | cut -d' ' -f1)"
    printf 'command_log_receipts=M209_LOGS.sha256\n'
    printf 'command_log_receipts_sha256=%s\n' "$logs_manifest_sha"
    printf 'command_log_integrity=%s\n' "$logs_status"
  } > "$post"
  if [[ "$source_compare" != PASS || "$lock_compare" != PASS ]]; then
    printf 'NATIVE WSL QUALIFICATION: integrity drift or collector failure (see %s)\n' "$post" >&2
    (( rc != 0 )) || rc=5
  fi
  printf 'NATIVE WSL QUALIFICATION: %s (exit %s); evidence: %s\n' "$phase" "$rc" "$evidence"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! source_members > "$evidence/M209_PRE_RUN_SOURCE.sha256"; then
  phase=PRE_RUN_SOURCE_SNAPSHOT_FAILED
  exit 5
fi
source_pre="$(sha256sum "$evidence/M209_PRE_RUN_SOURCE.sha256" | cut -d' ' -f1)"
pre_backend="$(sha256sum backend/uv.lock | cut -d' ' -f1)"
pre_frontend="$(sha256sum frontend/package-lock.json | cut -d' ' -f1)"
phase=RUNNING

# Run the original make owners, never an independent duplicate of their semantics.
run_step() {
  local label="$1" rc tee_rc
  local -a codes
  shift
  last_step="$label"
  printf '\n=== NATIVE WSL: %s ===\n' "$label"
  set +e
  "$@" 2>&1 | tee "$evidence/${label}.log"
  codes=("${PIPESTATUS[@]}")
  rc=${codes[0]}
  tee_rc=${codes[1]}
  set -e
  if (( tee_rc != 0 )); then
    printf 'NATIVE WSL: evidence logging failed for %s (tee exit %d)\n' "$label" "$tee_rc" >&2
    rc=6
  fi
  last_exit="$rc"
  if (( rc == 0 )); then
    printf '%s\t0\tPASS_COMMAND_ONLY\n' "$label" >> "$steps"
  else
    printf '%s\t%d\tFAILED_OR_BLOCKED\n' "$label" "$rc" >> "$steps"
    phase="STOPPED_AT_${label}"
    exit "$rc"
  fi
  if [[ "$label" == 02_sync ]] && \
     { [[ "$(sha256sum backend/uv.lock | cut -d' ' -f1)" != "$pre_backend" ]] || \
       [[ "$(sha256sum frontend/package-lock.json | cut -d' ' -f1)" != "$pre_frontend" ]]; }; then
    last_step=02_sync_lock_integrity
    last_exit=5
    phase=STOPPED_AT_LOCK_DRIFT
    printf '%s\t5\tLOCK_DRIFT\n' "$last_step" >> "$steps"
    exit 5
  fi
  # Detect changed canonical source immediately, before invoking another owner.
  if ! source_members > "$evidence/M209_STEP_SOURCE.tmp" || \
     ! cmp -s "$evidence/M209_PRE_RUN_SOURCE.sha256" "$evidence/M209_STEP_SOURCE.tmp"; then
    rm -f -- "$evidence/M209_STEP_SOURCE.tmp"
    last_step="${label}_source_integrity"
    last_exit=5
    phase="STOPPED_AT_SOURCE_DRIFT"
    printf '%s\t5\tSOURCE_DRIFT\n' "$last_step" >> "$steps"
    exit 5
  fi
  rm -f -- "$evidence/M209_STEP_SOURCE.tmp"

}

run_step 01_source_transport_clean make --no-print-directory source-transport-clean
run_step 02_sync make --no-print-directory sync
# M215: fail fast if the previously observed Schedule input regression recurs.
# The original Vitest owner runs after locked npm sync and before expensive gates.
# Its success does not omit any subsequent canonical verification owner.
run_step 02b_schedule_plan_inputs make --no-print-directory frontend-schedule-plan-input-regression
run_step 03_native_issuer_wheel bash scripts/qualify_native_issuer_wheel.sh
run_step 04_architecture make --no-print-directory architecture
run_step 05_verify_p0 make --no-print-directory verify-p0
run_step 06_test make --no-print-directory test
run_step 07_frontend_functional make --no-print-directory frontend-functional-certification

# Native certification is the original owner. No APP PASS can be inferred from
# a plain command exit or a diagnostic log (including a provisioning blocker).
last_step=08_certify
printf '\n=== NATIVE WSL: %s ===\n' "$last_step"
set +e
make --no-print-directory certify 2>&1 | tee "$evidence/08_certify.log"
cert_codes=("${PIPESTATUS[@]}")
cert_rc=${cert_codes[0]}
tee_rc=${cert_codes[1]}
set -e
last_exit="$cert_rc"
if (( tee_rc != 0 )); then
  certification=LOGGING_FAILED
  phase=STOPPED_AT_CERTIFY_LOGGING
  printf '%s\t6\tLOGGING_FAILED\n' "$last_step" >> "$steps"
  exit 6
fi
# M216: one JSON record, one parser result. Independent text fragments or
# malformed/contradictory records cannot be mislabeled as known provisioning.
# This classifier only explains a failed native command; it grants no authority.
if (( cert_rc == 2 )) && \
    node scripts/classify_native_fleet_blocker.cjs "$evidence/08_certify.log"; then
  certification=EXPECTED_PROVISIONING_BLOCKER_OBSERVED_NOT_APP_PASS
  phase=BLOCKED_AT_DEPLOYMENT_ENROLLMENT
  printf '%s\t2\tEXPECTED_PROVISIONING_BLOCKER_NOT_APP_PASS\n' "$last_step" >> "$steps"
  exit 2
fi
if (( cert_rc == 0 )); then
  certification=COMMAND_EXIT_0_APP_PROOF_UNVERIFIED
  phase=BLOCKED_PENDING_INDEPENDENT_APP_PASS
  printf '%s\t0\tCOMMAND_SUCCESS_NOT_APP_PASS\n' "$last_step" >> "$steps"
  exit 2
fi
certification=UNEXPECTED_CERTIFY_FAILURE
phase=STOPPED_AT_CERTIFY
printf '%s\t%d\tUNEXPECTED_FAILURE\n' "$last_step" "$cert_rc" >> "$steps"
(( cert_rc != 0 )) || cert_rc=2
exit "$cert_rc"
