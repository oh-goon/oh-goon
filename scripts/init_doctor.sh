#!/bin/sh
set -u

# Explicit first-checkout diagnostic only. This command never admits, repairs,
# retries, skips, or substitutes native uv/npm bootstrap execution.
failed=0

report_failure() {
  owner=$1
  selected=$2
  probed=$3
  reason=$4
  failed=1
  printf '[FAIL] %s: %s\n' "$owner" "$selected" >&2
  if [ "$probed" != "$selected" ]; then
    printf '       nearest existing parent: %s\n' "$probed" >&2
  fi
  printf '       reason: %s\n' "$reason" >&2
}

nearest_existing_dir() {
  candidate=$1
  while [ ! -e "$candidate" ] && [ "$candidate" != / ]; do
    candidate=${candidate%/*}
    [ -n "$candidate" ] || candidate=/
  done
  [ -d "$candidate" ] || return 1
  printf '%s\n' "$candidate"
}

probe_dir() {
  owner=$1
  selected=$2
  if [ -d "$selected" ]; then
    probed=$selected
  else
    probed=$(nearest_existing_dir "$selected") || {
      report_failure "$owner" "$selected" "$selected" 'nearest existing parent is not a directory'
      return
    }
  fi
  probe="$probed/.oh-goon-init-doctor-probe-$$"
  if ! reason=$( ( umask 077; : > "$probe" ) 2>&1 ); then
    report_failure "$owner" "$selected" "$probed" "${reason:-cannot create probe file}"
    return
  fi
  if ! reason=$(printf '1' | dd of="$probe" conv=fsync status=none 2>&1); then
    rm -f "$probe" 2>/dev/null || true
    report_failure "$owner" "$selected" "$probed" "${reason:-cannot write/fsync probe file}"
    return
  fi
  if ! reason=$(rm -f "$probe" 2>&1); then
    report_failure "$owner" "$selected" "$probed" "${reason:-cannot delete probe file}"
    return
  fi
  printf '[OK] %s: %s\n' "$owner" "$selected"
}

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

if [ -n "${UV_CACHE_DIR:-}" ]; then
  uv_cache=$UV_CACHE_DIR
else
  uv_cache=$(uv cache dir 2>/dev/null || true)
  if [ -z "$uv_cache" ]; then
    if [ -n "${XDG_CACHE_HOME:-}" ]; then
      uv_cache=$XDG_CACHE_HOME/uv
    else
      uv_cache=$HOME/.cache/uv
    fi
  fi
fi

npm_cache=''
if [ -n "${npm_config_cache:-}" ]; then
  npm_cache=$npm_config_cache
elif command -v npm >/dev/null 2>&1; then
  npm_cache=$(npm config get cache 2>/dev/null || true)
  [ "$npm_cache" != undefined ] || npm_cache=''
fi

printf '%s\n' 'OH-GOON INIT DOCTOR'
printf '%s\n' 'Mode: diagnostic only — this does not prove or admit native uv/npm bootstrap.'
probe_dir 'repository source/bootstrap state' "$root"
probe_dir 'backend local lock/environment' "$root/backend"
probe_dir 'frontend dependency materialization' "$root/frontend"
probe_dir 'native uv cache' "$uv_cache"
if [ -n "$npm_cache" ]; then
  probe_dir 'native npm cache' "$npm_cache"
fi

if [ "$failed" -ne 0 ]; then
  printf '%s\n' 'OH-GOON INIT DOCTOR: STORAGE PROBLEM OBSERVED' >&2
  printf '%s\n' 'Diagnostic only. No cache relocation, permission change, remount, retry, bootstrap evidence, or fallback was attempted.' >&2
  exit 73
fi
printf '%s\n' 'OH-GOON INIT DOCTOR: CURRENT PATH PROBES PASS'
printf '%s\n' 'Diagnostic only. Native uv/npm remain authoritative for whether bootstrap actually succeeds.'
