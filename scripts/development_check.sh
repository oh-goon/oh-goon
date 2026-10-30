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
  if [[ ! "$ROOT" -ef . ]]; then
    WORKSPACE_INTEGRITY_FAILED=1
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: declared checkout directory changed during make check' >&2
    return 1
  fi
  local current
  if ! current="$("$SOURCE_LAYOUT" identity --root "$ROOT")"; then
    WORKSPACE_INTEGRITY_FAILED=1
    return 1
  fi
  if [[ "$current" != "$WORKSPACE_SOURCE_IDENTITY" ]]; then
    WORKSPACE_INTEGRITY_FAILED=1
    printf '%s\n' 'WORKSPACE INTEGRITY FAILED: canonical authored source changed during make check' >&2
    printf 'Expected: %s\nObserved: %s\n' "$WORKSPACE_SOURCE_IDENTITY" "$current" >&2
    return 1
  fi
}

names=(); statuses=(); notes=(); failed=0
record(){ names+=("$1"); statuses+=("$2"); notes+=("${3:-}"); case "$2" in FAIL|BLOCKED) failed=1;; esac; }
run(){
  local name="$1" target="$2" rc=0
  if (( WORKSPACE_INTEGRITY_FAILED )); then
    record "$name" BLOCKED "source authority changed; check not started"
    return 1
  fi
  sh "$BANNER" "DEVELOPMENT CHECK" "Owner/check: $name | make $target"
  "$MAKE_BIN" --no-print-directory "$target" || rc=$?
  if ! verify_workspace_integrity; then
    if (( rc )); then record "$name" FAIL "exit $rc; source authority changed"; return "$rc"; fi
    record "$name" BLOCKED "source authority changed during check"
    return 1
  fi
  if (( rc )); then record "$name" FAIL "exit $rc"; return "$rc"; fi
  record "$name" PASS
}

# Each semantic check proves its own executable/dependency readiness. Do not predict
# backend availability with a separate --help probe before the real operation.
if capture_workspace_integrity; then
  run "Changed behavior" change-check
  if ! verify_workspace_integrity; then
    record "Workspace integrity" BLOCKED "source-owned workspace changed or disappeared; rerun make check"
  fi
else
  record "Workspace integrity" BLOCKED "canonical authored-source identity unavailable; no checks started"
fi

printf '\nOH-GOON DEVELOPMENT CHECK\n\n'
for i in "${!names[@]}"; do
  if [[ -n "${notes[$i]}" ]]; then printf '%-24s %-8s %s\n' "${names[$i]}" "${statuses[$i]}" "${notes[$i]}"; else printf '%-24s %s\n' "${names[$i]}" "${statuses[$i]}"; fi
done
printf '\n'
if (( failed )); then
  echo "RESULT: FAILED"
  echo "Focused change feedback failed; fix the reported owner before continuing."
  exit 1
fi
echo "RESULT: PASSED"
echo "FOCUSED / NOT_FULLY_VERIFIED: selected feedback passed; required merge proof and daily full remain separate."
