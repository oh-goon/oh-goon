#!/usr/bin/env bash
set -euo pipefail
# Preserve the original hosted native view and its owned cleanup around one selected command.
test "$#" -gt 0
test "${RUNNER_ENVIRONMENT:-}" = github-hosted
sdk_root=/usr/local/lib/android/sdk
if [ -e "$sdk_root" ] || [ -L "$sdk_root" ]; then
  test -d "$sdk_root"
  test "$(readlink -e "$sdk_root")" = "$sdk_root"
  if mountpoint -q -- "$sdk_root"; then
    echo "CI SDK overlay target is already mounted" >&2
    exit 1
  fi
  sdk_source="oh-goon-ci-sdk-$GITHUB_RUN_ID-$$"
  sdk_mounted=0
  cleanup_sdk() {
    check_status=$?
    trap - EXIT INT TERM
    if sdk_current_source=$(findmnt -rn --first-only --direction backward --target "$sdk_root" --output SOURCE) &&
      [ "$sdk_current_source" = "$sdk_source" ]; then
      if sudo umount -- "$sdk_root"; then
        echo "CI SDK overlay released"
      else
        echo "CI SDK overlay cleanup failed" >&2
        if [ "$check_status" = 0 ]; then check_status=1; fi
      fi
    elif [ "$sdk_mounted" = 1 ]; then
      echo "CI SDK overlay cleanup ownership is unverified" >&2
      if [ "$check_status" = 0 ]; then check_status=1; fi
    fi
    exit "$check_status"
  }
  trap cleanup_sdk EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  sudo mount -t tmpfs -o ro,nodev,nosuid,noexec,size=4k "$sdk_source" "$sdk_root"
  sdk_mounted=1
  findmnt -rn --first-only --direction backward --target "$sdk_root" --output SOURCE,FSTYPE,OPTIONS
  sdk_identity=$(stat -Lc '%d:%i' "$sdk_root")
  bwrap --unshare-user --unshare-pid --unshare-net --die-with-parent --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --proc /proc --dev /dev -- /usr/bin/python3 -I -S - "$sdk_identity" <<'PY'
import os
import sys
path = "/usr/local/lib/android/sdk"
metadata = os.stat(path)
assert f"{metadata.st_dev}:{metadata.st_ino}" == sys.argv[1]
assert os.statvfs(path).f_flag & os.ST_RDONLY
with os.scandir(path) as entries:
    assert next(entries, None) is None
print("Native SDK overlay identity, emptiness and read-only view verified")
PY
fi
"$@"
