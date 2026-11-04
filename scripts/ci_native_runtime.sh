#!/usr/bin/env bash
set -euo pipefail
# Preserve the original hosted view; qualification is an explicit additional context.
profile=${OH_GOON_NATIVE_QUALIFICATION_PROFILE:-}
case "$profile" in
  ''|shared-user-manager) ;;
  *) printf 'Unknown native qualification profile\n' >&2; exit 2 ;;
esac
needed=${profile:+true}
if [[ ${1-} == --native-resources-needed ]]; then
  [[ $# -ge 4 && ( $2 == true || $2 == false ) && $3 == -- ]] || {
    printf 'Native capability selection must be true or false followed by --\n' >&2
    exit 2
  }
  needed=$2
  shift 3
fi
test "$#" -gt 0
selected_command=("$@")
if [[ $needed == true ]]; then
  [[ $profile == shared-user-manager ]] || {
    printf 'Selected native resource tests require an explicit qualification profile\n' >&2
    exit 2
  }
  owner_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
  source "$owner_root/scripts/development_test_resources.sh"
  cpus=$(oh_goon_test_resolved_cpus "${OH_GOON_TEST_CPUS-0}") || exit 2
  export OH_GOON_TEST_CPUS=$cpus
  report_argument=()
  if [[ -n ${OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH:-} ]]; then
    report_argument=(--report "$OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH")
  fi
  selected_command=("$owner_root/backend/.venv/bin/python" -I \
    -m oh_goon.repository_tooling._native_test_worker \
    --root "$owner_root" --cpus "$cpus" "${report_argument[@]}" -- "$@")
fi
if [[ ${RUNNER_ENVIRONMENT:-} != github-hosted ]]; then
  [[ $profile == shared-user-manager ]] || exit 1
  exec "${selected_command[@]}"
fi
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
selected_pid=
requested_signal=
requested_status=0
forward_selected_signal() {
  requested_signal=$1
  requested_status=$2
  if [[ -n "$selected_pid" ]]; then
    kill -s "$requested_signal" -- "-$selected_pid" 2>/dev/null || true
  fi
}
trap 'forward_selected_signal INT 130' INT
trap 'forward_selected_signal TERM 143' TERM
# Native Bash job control preserves INT for the asynchronous command and owns
# its process group. Native wait can then receive entry-process cancellation.
set -m
"${selected_command[@]}" &
selected_pid=$!
set +m
if ((requested_status)); then
  forward_selected_signal "$requested_signal" "$requested_status"
fi
while :; do
  if wait -f "$selected_pid"; then selected_status=0; else selected_status=$?; fi
  # A trapped signal interrupts wait before the selected command has settled.
  if ! kill -0 "$selected_pid" 2>/dev/null; then break; fi
done
if ((requested_status)); then exit "$requested_status"; fi
exit "$selected_status"
