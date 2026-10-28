#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
MAKE_BIN="${MAKE:-make}"
BANNER="$ROOT/scripts/command_banner.sh"

WORKSPACE_SOURCE_IDENTITY=""
WORKSPACE_INTEGRITY_FAILED=0
SOURCE_LAYOUT="$ROOT/backend/.venv/bin/oh-goon-source-layout"

capture_workspace_integrity() {
  if [[ ! -x "$SOURCE_LAYOUT" ]]; then
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical source-layout owner is unavailable' >&2
    return 1
  fi
  WORKSPACE_SOURCE_IDENTITY="$("$SOURCE_LAYOUT" identity --root "$ROOT")" || return 1
  if [[ ! "$WORKSPACE_SOURCE_IDENTITY" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical source-layout owner returned invalid identity' >&2
    return 1
  fi
  printf 'WORKSPACE SOURCE IDENTITY: %s\n' "$WORKSPACE_SOURCE_IDENTITY"
}

verify_workspace_integrity() {
  (( WORKSPACE_INTEGRITY_FAILED == 0 )) || return 1
  if [[ -z "$WORKSPACE_SOURCE_IDENTITY" ]]; then
    return 0
  fi
  if [[ ! "$ROOT" -ef . ]]; then
    WORKSPACE_INTEGRITY_FAILED=1
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: declared checkout directory changed during make verify' >&2
    return 1
  fi
  local current
  if ! current="$("$SOURCE_LAYOUT" identity --root "$ROOT")"; then
    WORKSPACE_INTEGRITY_FAILED=1
    return 1
  fi
  if [[ "$current" != "$WORKSPACE_SOURCE_IDENTITY" ]]; then
    WORKSPACE_INTEGRITY_FAILED=1
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical authored source changed during make verify' >&2
    printf 'Expected: %s\nObserved: %s\n' "$WORKSPACE_SOURCE_IDENTITY" "$current" >&2
    return 1
  fi
}

# Exhaustive diagnostic breadth. This intentionally exceeds the lean APP certification
# Plan and never creates APP PASS.
VERIFY_ROWS=(
  "oh-goon/backend-tests|Backend complete|test-python-complete"
  "oh-goon/tooling-tests|Repository tooling complete|test-tooling-complete"
  "oh-goon/architecture|Architecture|architecture"
  "oh-goon/generated-contracts|Generated contracts|check-generated"
  "oh-goon/integration-tests|Backend integration|test-integration"
  "oh-goon/frontend-functional|Frontend functional|frontend-functional-certification"
  "oh-goon/backend-p1|Repository authoring / P1|verify-p1"
  "oh-goon/browser-harness-preflight|Browser harness preflight|browser-harness-preflight"
  "oh-goon/browser-engine-preflight|Browser engine preflight|browser-preflight"
  "oh-goon/browser-runtime-preflight|Browser runtime preflight|browser-runtime-preflight"
  "oh-goon/browser-targeted|Browser targeted|browser-targeted-certification"
  "oh-goon/browser-required|Browser required|browser-required-certification"
)

if [[ "${1:-}" == "--list" ]]; then
  printf '%s
' "${VERIFY_ROWS[@]}"
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "usage: $0 [--list]" >&2
  exit 2
fi

names=(); statuses=(); notes=(); failed=0
backend_ready=1
init_rc=0

sh "$BANNER" "APPLICATION VERIFY PREPARE" "Diagnostic prerequisite: make init | native uv/npm ownership | no APP authority"
"$MAKE_BIN" --no-print-directory init || init_rc=$?
if [[ "$init_rc" -ne 0 ]]; then
  backend_ready=0
fi

if [[ "$backend_ready" -eq 1 ]]; then
  if ! capture_workspace_integrity; then
    printf '%s\n' 'RESULT: FAILED' 'Canonical source identity unavailable; no verification checks started.' >&2
    exit 1
  fi
  sh "$BANNER" "APPLICATION VERIFY CONVERGENCE PREFLIGHT" "Fail-fast candidate coherence before expensive qualification"
  if ! "$MAKE_BIN" --no-print-directory candidate-convergence-preflight; then
    echo "Candidate convergence preflight failed; expensive verification was not started." >&2
    exit 1
  fi
  if ! verify_workspace_integrity; then
    printf '%s\n' 'RESULT: FAILED' 'Source authority changed during convergence; no verification checks started.' >&2
    exit 1
  fi
fi

record() {
  names+=("$1"); statuses+=("$2"); notes+=("${3:-}")
  case "$2" in FAIL|BLOCKED) failed=1 ;; esac
}

run_row() {
  local goon="$1" name="$2" target="$3"
  local rc=0
  if (( WORKSPACE_INTEGRITY_FAILED )); then
    record "$name" BLOCKED "source authority changed; check not started | $goon | make $target"
    return 1
  fi
  sh "$BANNER" "APPLICATION VERIFY" "Diagnostic breadth: $goon | make $target"
  "$MAKE_BIN" --no-print-directory "$target" || rc=$?
  if ! verify_workspace_integrity; then
    if (( rc )); then
      record "$name" FAIL "exit $rc; source authority changed | $goon | make $target"
      return "$rc"
    fi
    record "$name" BLOCKED "source authority changed during check | $goon | make $target"
    return 1
  fi
  if [[ "$rc" -eq 0 ]]; then
    record "$name" PASS
    return 0
  fi
  record "$name" FAIL "exit $rc | $goon | make $target"
  return "$rc"
}

block_row() { record "$2" BLOCKED "$4 | $1 | make $3"; }

for row in "${VERIFY_ROWS[@]}"; do
  IFS='|' read -r goon name target <<<"$row"

  if [[ "$backend_ready" -eq 0 ]]; then
    block_row "$goon" "$name" "$target" "native make init failed exit $init_rc; repair the native materialization failure and rerun make verify"
    continue
  fi

  run_row "$goon" "$name" "$target" || true
done

if ! verify_workspace_integrity; then
  record "Workspace integrity" BLOCKED "source-owned workspace changed or disappeared; rerun make verify"
fi

printf '
OH-GOON APPLICATION VERIFICATION

'
for i in "${!names[@]}"; do
  if [[ -n "${notes[$i]}" ]]; then
    printf '%-34s %-8s %s
' "${names[$i]}" "${statuses[$i]}" "${notes[$i]}"
  else
    printf '%-34s %s
' "${names[$i]}" "${statuses[$i]}"
  fi
done

printf '
'
if (( failed )); then
  echo "RESULT: FAILED"
  echo "This was diagnostic verification only; no APP certification PASS was created."
  echo "Fix the reported FAIL/BLOCKED areas and rerun: make verify"
  exit 1
fi

echo "RESULT: PASSED"
echo "All broad diagnostics passed. This is not certification authority."
echo "Final release-critical authority remains: make certify"
