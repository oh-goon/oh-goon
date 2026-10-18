#!/bin/sh
set -u
label=${1:-COMMAND}
detail=${2:-}
stream=${3:-stdout}
case "$stream" in
  stderr) fd=2 ;;
  stdout) fd=1 ;;
  *) printf 'command_banner.sh: stream must be stdout or stderr\n' >&2; exit 2 ;;
esac
if [ "$fd" -eq 2 ]; then printf '\n' >&2; else printf '\n'; fi
if [ -t "$fd" ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
  case "$label" in
    *FAILED*|*BLOCKED*) color=31 ;;
    *PASSED*|*READY*) color=32 ;;
    *RETRY*) color=33 ;;
    *) color=36 ;;
  esac
  heading=$(printf '\033[1;%sm=== %s ===\033[0m' "$color" "$label")
else
  heading="=== $label ==="
fi
if [ "$fd" -eq 2 ]; then
  printf '%s\n' "$heading" >&2
  [ -z "$detail" ] || printf '%s\n' "$detail" >&2
else
  printf '%s\n' "$heading"
  [ -z "$detail" ] || printf '%s\n' "$detail"
fi
