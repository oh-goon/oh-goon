#!/usr/bin/env bash
# Native source-transport cleanup shared by the bootstrap and Python frontdoors.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 || ( ${2-} != '' && ${2-} != --report0 ) ]]; then
  echo 'usage: source_transport_clean.sh ROOT [--report0]' >&2
  exit 2
fi
report0=${2-}
cd -- "$1"
inventory="$(mktemp)"
trap 'rm -f -- "$inventory"' EXIT

refuse() {
  local path=$1 reason=$2
  if [[ "$report0" == --report0 ]]; then
    printf '%s\0' "${path#./}"
  fi
  printf 'SOURCE TRANSPORT CLEAN BLOCKED: %s: %s\n' "$reason" "${path#./}" >&2
  exit 2
}

# Cleanup never enters dependency, Git, cache, build or operational state.
# A non-empty basename is required before every recognized transport suffix.
find . \
  \( -type d \( -name .git -o -name .venv -o -name node_modules \
    -o -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \
    -o -name .ruff_cache -o -name .hashmarks -o -name .oh-goon-data \
    -o -name .oh-goon-playwright-browsers -o -name .oh-goon-vite-cache \
    -o -name .eggs -o -name '*.egg-info' -o -name '*.dist-info' \
    -o -path ./frontend/coverage -o -path ./frontend/playwright-report \
    -o -path ./frontend/playwright-runtime-report -o -path ./frontend/test-results \
    -o -path ./coverage -o -path ./playwright-report -o -path ./playwright-runtime-report \
    -o -path ./test-results -o -path ./frontend/dist -o -path ./generated/release/dist \
    -o -path ./generated/release/static -o -path ./release-artifacts \) \) -prune -o \
  \( -name '?*.patch' -o -name '?*.patch.sha256' \
    -o -name '?*:Zone.Identifier' -o -name '?*:Zone.Identifier:$DATA' \) -print0 \
  > "$inventory"

# Establish eligibility for the entire captured inventory before the first unlink.
count=0
while IFS= read -r -d '' path; do
  if [[ -L "$path" || ! -f "$path" || "$(stat -c '%h' -- "$path")" != 1 ]]; then
    refuse "$path" 'non-regular transport artifact'
  fi
  case "$path" in
    *:Zone.Identifier|*':Zone.Identifier:$DATA')
      case "$path" in
        *:Zone.Identifier) owner="${path%:Zone.Identifier}" ;;
        *':Zone.Identifier:$DATA') owner="${path%:Zone.Identifier:\$DATA}" ;;
      esac
      if [[ -L "$owner" || ( ! -f "$owner" && ! -d "$owner" ) ]] \
          || { [[ -f "$owner" ]] && [[ "$(stat -c '%h' -- "$owner")" != 1 ]]; }; then
        refuse "$path" 'orphaned or unsafe Windows metadata owner'
      fi
      ;;
  esac
  (( count += 1 ))
done < "$inventory"

while IFS= read -r -d '' path; do
  rm -f -- "$path"
  if [[ "$report0" == --report0 ]]; then
    printf '%s\0' "${path#./}"
  fi
done < "$inventory"
if [[ "$report0" != --report0 ]]; then
  printf 'Removed %d disposable source transport artifacts\n' "$count"
fi
