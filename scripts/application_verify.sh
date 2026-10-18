#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MAKE_BIN="${MAKE:-make}"
BANNER="$ROOT/scripts/command_banner.sh"

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
  sh "$BANNER" "APPLICATION VERIFY CONVERGENCE PREFLIGHT" "Fail-fast candidate coherence before expensive qualification"
  if ! "$MAKE_BIN" --no-print-directory candidate-convergence-preflight; then
    echo "Candidate convergence preflight failed; expensive verification was not started." >&2
    exit 1
  fi
fi

record() {
  names+=("$1"); statuses+=("$2"); notes+=("${3:-}")
  case "$2" in FAIL|BLOCKED) failed=1 ;; esac
}

run_row() {
  local goon="$1" name="$2" target="$3"
  sh "$BANNER" "APPLICATION VERIFY" "Diagnostic breadth: $goon | make $target"
  local rc=0
  "$MAKE_BIN" --no-print-directory "$target" || rc=$?
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
