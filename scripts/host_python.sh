#!/usr/bin/env bash
# Select the interpreter for hosted, diagnostic-only Python execution.
#
# An explicit PYTHON is authoritative. Otherwise prefer the first PATH interpreter
# that actually exposes the stdlib capability machinery used by the repository-owned diagnostic probe.
# This deliberately does not inspect uv's artifact cache: host package availability
# and native project materialization are separate evidence domains.

select_host_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return 0
  fi

  local candidate resolved selector_dir repository_root capability_probe
  selector_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  repository_root="$(cd -- "$selector_dir/.." && pwd -P)"
  capability_probe="$repository_root/backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py"

  local -A seen=()
  for candidate in python python3; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || continue
    [[ -z "${seen[$resolved]:-}" ]] || continue
    seen[$resolved]=1
    if PYTHONNOUSERSITE=1 "$resolved" -I "$capability_probe" --probe-required-host-capabilities >/dev/null 2>&1; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done

  # Preserve the old failure path/message when no capable interpreter was found.
  # The capability boundary will classify the exact missing/incompatible package.
  command -v python3 2>/dev/null || command -v python 2>/dev/null || printf '%s\n' python3
}
