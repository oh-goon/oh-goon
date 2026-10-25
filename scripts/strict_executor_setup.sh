#!/usr/bin/env bash
set -u

uid_now="$(id -u)"
user_now="$(id -un)"
runtime_dir="/run/user/$uid_now"
bus="$runtime_dir/bus"

is_wsl=0
if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null || grep -qi microsoft /proc/version 2>/dev/null; then
  is_wsl=1
fi

printf 'OH-GOON STRICT EXECUTOR HOST SETUP\n\n'

if [[ "$(cat /proc/1/comm 2>/dev/null || true)" != "systemd" ]]; then
  echo '[BLOCKED] systemd is not PID 1.'
  if (( is_wsl )); then
    echo 'Enable WSL systemd in /etc/wsl.conf:'
    echo '  [boot]'
    echo '  systemd=true'
    echo 'Then run from Windows PowerShell: wsl --shutdown'
  fi
  exit 2
fi
printf '[OK] systemd is PID 1\n'

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$runtime_dir}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$bus}"

if [[ -S "$bus" ]] && systemctl --user show-environment >/dev/null 2>&1; then
  printf '[OK] systemd user manager already available\n'
else
  if (( ! is_wsl )); then
    echo '[BLOCKED] systemd user manager is unavailable.'
    echo 'This automatic setup is intentionally limited to WSL.'
    exit 2
  fi
  printf '[SETUP] preparing persistent WSL systemd user manager (sudo required once)\n'
  if ! sudo loginctl enable-linger "$user_now"; then
    echo '[BLOCKED] sudo/loginctl could not enable the persistent systemd user manager.'
    exit 2
  fi
  if ! sudo systemctl start "user-runtime-dir@${uid_now}.service" "user@${uid_now}.service"; then
    echo '[BLOCKED] could not start the systemd user runtime/manager.'
    exit 2
  fi
fi

if [[ ! -S "$bus" ]]; then
  echo "[BLOCKED] systemd user bus was not created: $bus"
  exit 2
fi
printf '[OK] systemd user bus: %s\n' "$bus"

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo '[BLOCKED] systemctl --user still cannot reach the user manager.'
  exit 2
fi
printf '[OK] systemctl --user reachable\n'

if ! systemd-run --user --quiet --wait --pipe --collect --property=Type=exec --property=Delegate=yes /usr/bin/true >/dev/null; then
  echo '[BLOCKED] systemd user manager cannot create a Delegate=yes transient unit.'
  exit 2
fi
printf '[OK] Delegate=yes transient unit available\n\n'
printf 'Native systemd delegation preparation complete.\n'
printf 'This does not provision Fleet enrollment or prove independent kernel custody.\n'
printf 'Strict execution still requires its original deployment authority; delegation alone cannot enable it.\n'
