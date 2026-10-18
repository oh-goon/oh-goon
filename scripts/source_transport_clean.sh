#!/usr/bin/env bash
# First native qualification step: stdlib-free, network-free transport cleanup.
# This must run before backend build isolation, uv.lock materialization or npm ci.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ ! -f "$root/backend/pyproject.toml" || ! -f "$root/VERSION" ]]; then
  echo 'SOURCE TRANSPORT CLEAN BLOCKED: not an OH-goon source root' >&2
  exit 2
fi
inventory="$(mktemp)"
trap 'rm -f -- "$inventory"' EXIT
# Ignore dependency, Git, build, cache and operational materialization roots.
# Exact candidate selection matches the repository's narrow transport metadata suffixes.
find "$root" \
  \( -type d \( -name .git -o -name .venv -o -name node_modules \
    -o -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \
    -o -name .ruff_cache -o -name .hashmarks -o -name .oh-goon-data \
    -o -name .oh-goon-playwright-browsers -o -name .oh-goon-vite-cache \) \) -prune -o \
  \( -name '*.patch' -o -name '*.patch.sha256' \
    -o -name '*:Zone.Identifier' -o -name '*:Zone.Identifier:$DATA' \) -print0 \
  > "$inventory"
# Prevalidate *all* candidates so a malformed source path cannot cause a
# partially completed cleanup under the appearance of success.
count=0
while IFS= read -r -d '' path; do
  if [[ -L "$path" || ! -f "$path" || "$(stat -c '%h' -- "$path")" != 1 ]]; then
    printf 'SOURCE TRANSPORT CLEAN BLOCKED: non-regular transport artifact: %s\n' \
      "${path#"$root"/}" >&2
    exit 2
  fi
  # The canonical source-transport owner classifies exact Zone.Identifier
  # suffixes by path, NOT by INI payload. On WSL, Windows ZIP extractors can
  # materialize empty, UTF-16, MOTW, or binary ADS payloads. These bytes are
  # transport data only and cannot decide authored source membership.
  # Require an actual non-symlink sibling (file or directory), and never
  # remove orphaned, symlinked, or multiply linked candidates.
  case "$path" in
    *:Zone.Identifier|*':Zone.Identifier:$DATA')
      case "$path" in
        *:Zone.Identifier) owner="${path%:Zone.Identifier}" ;;
        *':Zone.Identifier:$DATA') owner="${path%:Zone.Identifier:\$DATA}" ;;
      esac
      if [[ -L "$owner" || ( ! -f "$owner" && ! -d "$owner" ) ]] \
          || { [[ -f "$owner" ]] && [[ "$(stat -c '%h' -- "$owner")" != 1 ]]; }; then
        printf 'SOURCE TRANSPORT CLEAN BLOCKED: orphaned or unsafe Windows metadata owner: %s\n' \
          "${path#"$root"/}" >&2
        exit 2
      fi
      ;;
  esac
  (( count += 1 ))
done < "$inventory"
while IFS= read -r -d '' path; do
  rm -f -- "$path"
done < "$inventory"
printf 'Removed %d disposable source transport artifacts\n' "$count"
