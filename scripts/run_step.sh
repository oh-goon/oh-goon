#!/bin/sh
# OH_GOON_FORWARD_ARGV_FROM: 4
set -u

label=${1:-STEP}
detail=${2:-}
shift 2 || true
if [ "${1:-}" != "--" ]; then
  printf 'run_step.sh: expected -- before command\n' >&2
  exit 2
fi
shift
if [ "$#" -eq 0 ]; then
  printf 'run_step.sh: missing command\n' >&2
  exit 2
fi

sh scripts/command_banner.sh "$label" "$detail"
monotonic_seconds() {
  # Timing is presentation metadata, never execution authority. Prefer Linux/WSL
  # procfs so a sandbox that intentionally denies the external `date` command
  # cannot turn an otherwise-successful Step into a failure.
  if [ -r /proc/uptime ]; then
    IFS=' ' read -r uptime_seconds _rest < /proc/uptime || return 1
    case "$uptime_seconds" in
      *.*) printf '%s\n' "${uptime_seconds%%.*}" ;;
      *[!0-9]*|'') return 1 ;;
      *) printf '%s\n' "$uptime_seconds" ;;
    esac
    return 0
  fi
  # Presentation timing never expands execution authority. If procfs is unavailable,
  # omit duration metadata rather than launching an external clock command.
  return 1
}

start=$(monotonic_seconds 2>/dev/null) || start=
"$@"
status=$?
end=$(monotonic_seconds 2>/dev/null) || end=
duration=
if [ -n "$start" ] && [ -n "$end" ]; then
  elapsed=$((end - start))
  hours=$((elapsed / 3600))
  minutes=$(((elapsed % 3600) / 60))
  seconds=$((elapsed % 60))
  if [ "$hours" -gt 0 ]; then
    duration="${hours}h ${minutes}m ${seconds}s"
  elif [ "$minutes" -gt 0 ]; then
    duration="${minutes}m ${seconds}s"
  else
    duration="${seconds}s"
  fi
fi
if [ "$status" -eq 0 ]; then
  if [ -n "$duration" ]; then
    printf '%s\n' "Step completed in ${duration}"
  else
    printf '%s\n' "Step completed"
  fi
else
  if [ -n "$duration" ]; then
    printf '%s\n' "Step failed after ${duration} (exit ${status})" >&2
  else
    printf '%s\n' "Step failed (exit ${status})" >&2
  fi
fi
exit "$status"
