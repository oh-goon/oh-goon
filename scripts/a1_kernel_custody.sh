#!/usr/bin/env bash
# A1L: optional root-enabled Linux smoke with complete (not skipped) JUnit.
# Read-only diagnostic, distinct from the M209 WSL original qualifier.
set -Eeuo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if (( $# != 2 )); then
  printf 'A1 KERNEL CUSTODY BLOCKED: evidence directory and exact test path required\n' >&2
  exit 2
fi
if [[ "$(id -u)" != 0 || "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]] || \
   ! command -v uv >/dev/null || ! command -v setpriv >/dev/null || \
   [[ ! -x "$root/backend/.venv/bin/oh-goon-qualification-exec" || ! -x "$root/backend/.venv/bin/oh-goon-a1-kernel-custody-receipt" ]]; then
  printf 'A1 KERNEL CUSTODY BLOCKED: native Linux root, x86_64, uv, setpriv and locked backend required\n' >&2
  exit 2
fi
if [[ "$2" != scripts/tests/test_a1h_real_linux_process_custody.py ]]; then
  printf 'A1 KERNEL CUSTODY BLOCKED: unexpected process test owner\n' >&2
  exit 2
fi
evidence="$(realpath -m -- "$1")"
if [[ "$evidence" == "$root" || "$evidence" == "$root/"* || -e "$evidence" || -L "$evidence" || ! -d "$(dirname "$evidence")" ]]; then
  printf 'A1 KERNEL CUSTODY BLOCKED: evidence must be a fresh external directory\n' >&2
  exit 2
fi
# The root-running test may not place writable evidence below an attacker-owned
# parent: a directory rename there could substitute receipts after completion.
parent="$(dirname "$evidence")"
parent_uid="$(stat -c '%u' -- "$parent")"
parent_mode="$(stat -c '%a' -- "$parent")"
if [[ "$parent_uid" != 0 || -L "$parent" ]] || (( (8#$parent_mode & 0022) != 0 )); then
  printf 'A1 KERNEL CUSTODY BLOCKED: root-owned, non-group/world-writable evidence parent required\n' >&2
  exit 2
fi
mkdir -- "$evidence"
cd "$root"
set +e
backend/.venv/bin/oh-goon-qualification-exec pytest -q -c backend/pyproject.toml \
  --junitxml "$evidence/A1_KERNEL_JUNIT.xml" "$2" 2>&1 | tee "$evidence/A1_KERNEL_RAW.log"
codes=("${PIPESTATUS[@]}")
set -e
if (( codes[1] != 0 )); then
  printf 'A1 KERNEL CUSTODY BLOCKED: cannot retain raw log\n' >&2
  exit 6
fi
set +e
backend/.venv/bin/oh-goon-a1-kernel-custody-receipt \
  --junit "$evidence/A1_KERNEL_JUNIT.xml" --log "$evidence/A1_KERNEL_RAW.log" \
  --pytest-exit "${codes[0]}" > "$evidence/A1_KERNEL_REVIEW.json"
result=$?
set -e
cat "$evidence/A1_KERNEL_REVIEW.json"
if (( result != 0 )); then
  printf 'A1 KERNEL CUSTODY BLOCKED: incomplete or skipped real process evidence\n' >&2
  exit 2
fi
printf 'A1 KERNEL CUSTODY: three real-kernel tests completed; NOT deployed custody attestation: %s\n' "$evidence"
