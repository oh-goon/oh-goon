#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MAKE_BIN="${MAKE:-make}"
BANNER="$ROOT/scripts/command_banner.sh"
CONSTRAINED_HOST="${OH_GOON_CONSTRAINED_HOST:-0}"
RUN_HOSTED_DIAGNOSTIC="${OH_GOON_RUN_HOSTED_DIAGNOSTIC:-0}"

case "$CONSTRAINED_HOST" in
  0|1) ;;
  *) printf '%s\n' "DEVELOPMENT TEST CONFIGURATION ERROR: OH_GOON_CONSTRAINED_HOST must be 0 or 1" >&2; exit 2 ;;
esac
case "$RUN_HOSTED_DIAGNOSTIC" in
  0|1) ;;
  *) printf '%s\n' "DEVELOPMENT TEST CONFIGURATION ERROR: OH_GOON_RUN_HOSTED_DIAGNOSTIC must be 0 or 1" >&2; exit 2 ;;
esac

HOSTED_DIAGNOSTIC_RECEIPT=""
HOSTED_DIAGNOSTIC_STATUS=""
if [[ -n "${OH_GOON_RUN_LOG_DIR:-}" ]]; then
  HOSTED_DIAGNOSTIC_RECEIPT="$OH_GOON_RUN_LOG_DIR/test/hosted-diagnostic-receipt.json"
  HOSTED_DIAGNOSTIC_STATUS="$OH_GOON_RUN_LOG_DIR/test/hosted-diagnostic-exit.status"
  if ! rm -f -- "$HOSTED_DIAGNOSTIC_STATUS"; then
    printf '%s\n' "DEVELOPMENT TEST CONFIGURATION ERROR: could not invalidate diagnostic status" >&2
    exit 2
  fi
  if ! rm -f -- "$HOSTED_DIAGNOSTIC_RECEIPT"; then
    printf '%s\n' "DEVELOPMENT TEST CONFIGURATION ERROR: could not invalidate hosted diagnostic receipt: $HOSTED_DIAGNOSTIC_RECEIPT" >&2
    exit 2
  fi
fi

if [[ "$ROOT" == /mnt/?/* ]] && grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  printf '%s\n' \
    "[NOTE] WSL Windows-mounted workspace detected: $ROOT" \
    "       Node/Python test startup can be substantially faster from the WSL filesystem (for example ~/oh-goon)." \
    "       This is a performance note only; development tests continue normally."
fi

names=()
statuses=()
notes=()
failed=0

WORKSPACE_SOURCE_IDENTITY=""
SOURCE_LAYOUT="$ROOT/backend/.venv/bin/oh-goon-source-layout"

capture_workspace_integrity() {
  if [[ ! -x "$SOURCE_LAYOUT" ]]; then
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical source-layout owner is unavailable' >&2
    return 1
  fi
  WORKSPACE_SOURCE_IDENTITY="$($SOURCE_LAYOUT identity)" || return 1
  if [[ "$WORKSPACE_SOURCE_IDENTITY" != sha256:* ]]; then
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical source-layout owner returned invalid identity' >&2
    return 1
  fi
}

verify_workspace_integrity() {
  if [[ -z "$WORKSPACE_SOURCE_IDENTITY" ]]; then
    return 0
  fi
  local current
  current="$($SOURCE_LAYOUT identity)" || return 1
  if [[ "$current" != "$WORKSPACE_SOURCE_IDENTITY" ]]; then
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical authored source changed during make test' >&2
    printf 'Expected: %s\nObserved: %s\n' "$WORKSPACE_SOURCE_IDENTITY" "$current" >&2
    return 1
  fi
}

frontend_failure_guard() {
  if ! verify_workspace_integrity; then
    record "Frontend prerequisite integrity" BLOCKED "source-owned workspace changed or disappeared"
    return 1
  fi
  return 0
}


record() {
  names+=("$1")
  statuses+=("$2")
  notes+=("${3:-}")
  case "$2" in
    FAIL|BLOCKED) failed=1 ;;
  esac
}

run_check() {
  local name="$1"
  local target="$2"
  sh "$BANNER" "DEVELOPMENT TEST" "Owner/check: $name | make $target"
  if "$MAKE_BIN" --no-print-directory "$target"; then
    record "$name" PASS
    return 0
  else
    local code=$?
    record "$name" FAIL "exit $code"
    return "$code"
  fi
}

skip_check() {
  record "$1" SKIPPED "$2"
}

run_optional_diagnostic() {
  local name="$1"
  local target="$2"
  sh "$BANNER" "DEVELOPMENT TEST" "Owner/check: $name | make $target"
  "$MAKE_BIN" --no-print-directory "$target"
  local code=$?
  if (( code == 0 )); then
    record "$name" PASS
    return 0
  fi
  # Make maps all recipe failures to exit 2. Only the original diagnostic's
  # exact status 3 may be treated as capability-unavailable, and only with a
  # current, regular, non-symlink status emitted by that invocation. Genuine
  # test/configuration failures must never be silently skipped.
  local native_code="$code"
  if [[ "$code" != 3 && -n "$HOSTED_DIAGNOSTIC_STATUS" && \
        -f "$HOSTED_DIAGNOSTIC_STATUS" && ! -L "$HOSTED_DIAGNOSTIC_STATUS" ]]; then
    local owned_status
    IFS= read -r owned_status < "$HOSTED_DIAGNOSTIC_STATUS" || true
    if [[ "$owned_status" == 3 ]]; then
      native_code=3
    fi
  fi
  if (( native_code == 3 )); then
    # Only explicitly requested hosted diagnostics reach this branch. An
    # unavailable requested test surface cannot be described as a passing or
    # implicitly skipped diagnostic, irrespective of native check results.
    record "$name" BLOCKED "requested host diagnostic unavailable; native backend checks are separate evidence"
    return 0
  fi
  record "$name" FAIL "exit $code"
  return 0
}

probe_backend_readiness() {
  sh "$BANNER" "DEVELOPMENT CAPABILITY PROBE" "Owner: materialized backend | existing executable readiness; acquisition forbidden"
  if "$MAKE_BIN" --no-print-directory backend-dependencies-check >/dev/null 2>&1; then
    record "Backend dependencies" PASS "existing local native backend capability detected"
    return 0
  fi
  record "Backend dependencies" SKIPPED "local native backend capability unavailable; no acquisition attempted"
  return 1
}

backend_ready=0

# Normal development runs only the existing, native uv/.venv project owners.
# The *independent system-Python* diagnostic is useful on constrained hosts,
# but automatically running it after native dependency synchronization gives
# misleading 'missing packages' and Make-failure output for a non-gate.
# Explicit opt-in or constrained-host mode is the only entry to this separate
# evidence surface; no host probe runs at all on the default path.
if [[ "$CONSTRAINED_HOST" == 1 || "$RUN_HOSTED_DIAGNOSTIC" == 1 ]]; then
  source "$ROOT/scripts/host_python.sh"
  HOST_PYTHON="$(select_host_python)"
  if command -v "$HOST_PYTHON" >/dev/null 2>&1; then
    run_optional_diagnostic "Host-only diagnostic" test-python-core-diagnostic
  else
    record "Host-only diagnostic" BLOCKED "requested host interpreter unavailable; native backend checks are separate evidence"
  fi
else
  skip_check "Host-only diagnostic" "not requested; make test uses authoritative native uv/.venv tests"
fi
if [[ "$CONSTRAINED_HOST" == 1 ]]; then
  probe_backend_readiness && backend_ready=1
else
  # Normal development executes the actual semantic checks directly. Each target
  # owns its own executable/dependency failure; do not predict readiness with an
  # unrelated `oh-goon --help` oracle.
  backend_ready=1
fi

if (( backend_ready )); then
  if ! capture_workspace_integrity; then
    record "Workspace integrity" BLOCKED "canonical authored-source identity unavailable"
    backend_ready=0
  fi
fi

if (( backend_ready )); then
  run_check "Backend fast unit" test-python
  run_check "Repository tooling" test-tooling
  run_check "Architecture" architecture
  run_check "Generated contracts" check-generated
  run_check "Backend integration" test-integration
else
  if [[ "$CONSTRAINED_HOST" == 1 ]]; then
    backend_skip_reason="diagnostic mode; local native backend capability unavailable; covered only by bounded hosted diagnostic where selected"
  else
    backend_skip_reason="backend dependencies blocked"
  fi
  skip_check "Backend fast unit" "$backend_skip_reason"
  skip_check "Repository tooling" "$backend_skip_reason"
  skip_check "Architecture" "$backend_skip_reason"
  skip_check "Generated contracts" "$backend_skip_reason"
  skip_check "Backend integration" "$backend_skip_reason"
fi

# Frontend checks are intentionally deferred until backend/tooling checks finish,
# but readiness is not predicted from node_modules shape. Each native npm-backed
# target is its own semantic dependency/tool authority and independent checks continue.
if verify_workspace_integrity; then
  run_check "Frontend typecheck" frontend-typecheck || frontend_failure_guard || true
  run_check "Frontend unit" test-frontend || frontend_failure_guard || true
  run_check "Frontend architecture" frontend-architecture || frontend_failure_guard || true
  run_check "Frontend i18n" i18n-check || frontend_failure_guard || true
  run_check "Frontend build" frontend-build || frontend_failure_guard || true
else
  record "Frontend prerequisite integrity" BLOCKED "source-owned workspace changed or disappeared during backend/tooling tests"
fi

printf '\nOH-GOON DEVELOPMENT TESTS\n\n'
for i in "${!names[@]}"; do
  if [[ -n "${notes[$i]}" ]]; then
    printf '%-24s %-8s %s\n' "${names[$i]}" "${statuses[$i]}" "${notes[$i]}"
  else
    printf '%-24s %s\n' "${names[$i]}" "${statuses[$i]}"
  fi
done

printf '\n'
if (( failed )); then
  echo "RESULT: FAILED"
  echo "Independent checks were allowed to continue; fix the reported FAIL/BLOCKED areas and rerun make test."
  exit 1
fi

if [[ "$CONSTRAINED_HOST" == 1 ]]; then
  echo "RESULT: HOSTED-DIAGNOSTIC-PASSED"
  echo "Hosted diagnostic checks passed. Any already-ready local backend/frontend development owners were also exercised; native certification remains unproven by design."
else
  echo "RESULT: PASSED"
  echo "Development checks passed. Strict executor/browser/self-certification are separate boundaries."
fi
