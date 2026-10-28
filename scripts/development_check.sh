#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MAKE_BIN="${MAKE:-make}"
BANNER="$ROOT/scripts/command_banner.sh"

names=(); statuses=(); notes=(); failed=0
record(){ names+=("$1"); statuses+=("$2"); notes+=("${3:-}"); case "$2" in FAIL|BLOCKED) failed=1;; esac; }
run(){ local name="$1" target="$2"; sh "$BANNER" "DEVELOPMENT CHECK" "Owner/check: $name | make $target"; if "$MAKE_BIN" --no-print-directory "$target"; then record "$name" PASS; else local rc=$?; record "$name" FAIL "exit $rc"; fi; }

# Each semantic check proves its own executable/dependency readiness. Do not predict
# backend availability with a separate --help probe before the real operation.
run "Backend fast unit" test-python
run "Repository tooling" test-tooling
run "Architecture" architecture
run "Generated contracts" check-generated

# Frontend package readiness is not predicted here. The native npm-backed targets
# below are the semantic authority and report their own dependency/tool failures.
run "Frontend typecheck" frontend-typecheck
run "Frontend unit" test-frontend

printf '\nOH-GOON DEVELOPMENT CHECK\n\n'
for i in "${!names[@]}"; do
  if [[ -n "${notes[$i]}" ]]; then printf '%-24s %-8s %s\n' "${names[$i]}" "${statuses[$i]}" "${notes[$i]}"; else printf '%-24s %s\n' "${names[$i]}" "${statuses[$i]}"; fi
done
printf '\n'
if (( failed )); then
  echo "RESULT: FAILED"
  echo "Development feedback only; run make verify when you need whole-app diagnosis."
  exit 1
fi
echo "RESULT: PASSED"
echo "Development checks passed. Run make verify for exhaustive diagnosis; make certify remains final authority."
