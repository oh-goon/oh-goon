#!/usr/bin/env bash
set -u

if (( $# < 2 )); then
  echo "usage: run_logged.sh <label> <command> [args...]" >&2
  exit 2
fi

label="$1"
shift

script_dir="${BASH_SOURCE[0]%/*}"
[[ "$script_dir" == "${BASH_SOURCE[0]}" ]] && script_dir=.
ROOT="$(cd "$script_dir/.." && pwd -P)"

# Transient progress is not durable run evidence. Preserve one direct presentation
# descriptor before stdout/stderr enter the sanitize/log pipeline so long-running
# certification can prove liveness to an interactive caller even if a downstream
# logger/UI batches the ordinary command stream. The child may use only this
# descriptor for bounded, already-safe progress labels; normal output still flows
# through sanitize_stream and tee.
exec 3>&1
export OH_GOON_PROGRESS_FD=3

# The logging wrapper owns streaming behavior. Python child processes otherwise
# become block-buffered behind tee and can make long certification phases look hung.
export PYTHONUNBUFFERED=1
if [[ -n "${OH_GOON_RUN_LOG_DIR:-}" ]]; then
  base="$OH_GOON_RUN_LOG_DIR"
else
  state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
  repo_key="$(printf '%s' "$ROOT" | sha256sum | cut -c1-16)"
  base="$state_home/oh-goon/run-logs/$repo_key"
fi
case "$base" in
  /*) ;;
  *) base="$ROOT/$base" ;;
esac
log_dir="$base/$label"

# Select the prepared project interpreter once. Selection never runs the command,
# so an unavailable uv/environment can safely continue with bounded console output.
# Log acquisition then uses native no-follow directory handles before re-entering
# this same command/cancellation owner with pinned descriptors.
if [[ -z "${OH_GOON_RUN_LOG_BOOTSTRAPPED:-}" ]]; then
  logging_python=""
  if [[ -x "$ROOT/backend/.venv/bin/python" ]] && command -v uv >/dev/null 2>&1; then
    logging_python="$(uv run --offline --frozen --no-sync --project "$ROOT/backend" oh-goon-run-log --interpreter 2>/dev/null)" || logging_python=""
  fi
  if [[ -n "$logging_python" && "$logging_python" != *$'\n'* && -x "$logging_python" ]]; then
    exec "$logging_python" -m oh_goon.repository_tooling.run_logging "$ROOT" "$base" "$label" "$@"
  fi
  OH_GOON_RUN_LOG_UNAVAILABLE=1
fi
logging_python="${OH_GOON_RUN_LOG_PYTHON:-}"
run_log_dir_fd="${OH_GOON_RUN_LOG_DIRECTORY_FD:-}"
run_log_fd="${OH_GOON_RUN_LOG_FILE_FD:-}"
active_name="${OH_GOON_RUN_LOG_ACTIVE_NAME:-}"
timestamp="${OH_GOON_RUN_LOG_TIMESTAMP:-}"
unset OH_GOON_RUN_LOG_BOOTSTRAPPED OH_GOON_RUN_LOG_PYTHON OH_GOON_RUN_LOG_DIRECTORY_FD
unset OH_GOON_RUN_LOG_FILE_FD OH_GOON_RUN_LOG_ACTIVE_NAME OH_GOON_RUN_LOG_TIMESTAMP

sanitize_stream() {
  if [[ -n "$logging_python" ]]; then
    "$logging_python" -m oh_goon.repository_tooling.run_logging --stream "$ROOT" "$base" "$HOME" -1
    return $?
  fi
  # The pre-environment path must also bound newline-free output. Carry only a
  # fixed-path overlap; discard an already-replaced temporary-token suffix.
  local chunk pending="" buffer tmp_match suffix rc boundary discard_tmp=0 overlap=9
  local state_label="<state>/run-logs" LC_ALL=C
  (( ${#ROOT} > overlap )) && overlap=${#ROOT}
  (( ${#base} > overlap )) && overlap=${#base}
  (( ${#HOME} > overlap )) && overlap=${#HOME}
  while :; do
    chunk=""
    IFS= read -r -n 8192 chunk
    rc=$?
    if (( rc == 0 && ${#chunk} < 8192 )); then chunk+=$'\n'; fi
    if (( discard_tmp )); then
      if [[ "$chunk" =~ ^[A-Za-z0-9._-]+ ]]; then
        suffix="${BASH_REMATCH[0]}"
        chunk="${chunk#"$suffix"}"
      fi
      [[ -n "$chunk" ]] && discard_tmp=0
    fi
    buffer="$pending$chunk"
    buffer="${buffer//"$ROOT"/<repo>}"
    buffer="${buffer//"$base"/"$state_label"}"
    buffer="${buffer//"$HOME"/<home>}"
    while [[ "$buffer" =~ /tmp/tmp\.[A-Za-z0-9._-]+ ]]; do
      tmp_match="${BASH_REMATCH[0]}"
      [[ "$buffer" == *"$tmp_match" ]] && discard_tmp=1
      buffer="${buffer//"$tmp_match"/<tmp>}"
    done
    boundary=$((${#buffer} - overlap))
    (( boundary < 0 )) && boundary=0
    suffix="${buffer##*$'\n'}"
    (( ${#buffer} - ${#suffix} > boundary )) && boundary=$((${#buffer} - ${#suffix}))
    if (( rc != 0 )); then boundary=${#buffer}; fi
    printf '%s' "${buffer:0:boundary}"
    pending="${buffer:boundary}"
    (( rc != 0 )) && break
  done
}

sh "$ROOT/scripts/command_banner.sh" "OH-GOON ${label^^}" "Owner: run_logged.sh | Command arguments omitted from diagnostics" | sanitize_stream

if [[ -n "${OH_GOON_RUN_LOG_UNAVAILABLE:-}" ]]; then
  printf 'WARNING: private persistent run logging is unavailable; prepare the uv environment and select an owned private directory without symlinks.\n' >&2
  echo "Continuing without persistent command logging." >&2
fi
unset OH_GOON_RUN_LOG_UNAVAILABLE

# Bash owns wall-clock metadata without an external `date` process. Certification
# sandboxes may deny unrelated executables; logging metadata must never become an
# execution dependency.
[[ -n "$timestamp" ]] || TZ=UTC printf -v timestamp '%(%Y%m%dT%H%M%SZ)T' -1
printf -v started_epoch '%(%s)T' -1
run_log="$log_dir/${timestamp}-$$.log"
active_run_log="/proc/self/fd/$run_log_dir_fd/$active_name"
latest_log="$log_dir/latest.log"
latest_candidate="/proc/self/fd/$run_log_dir_fd/.latest.${timestamp}.$$.tmp"

# The invocation owns exactly one byte stream: its active run log. Publish
# latest.log as a hard-link projection of that owner-scoped inode, then replace
# the previous projection with one same-directory atomic rename. A later
# invocation can therefore supersede this projection without giving an older
# writer any pathname through which it can mutate the newer run.
latest_projection_ok=0
if [[ -n "$run_log_fd" ]]; then
  if ! ln "$active_run_log" "$latest_candidate" 2>/dev/null || \
     ! mv -fT "$latest_candidate" "/proc/self/fd/$run_log_dir_fd/latest.log" 2>/dev/null; then
    rm -f "$latest_candidate" 2>/dev/null || true
    printf '\nWARNING: latest run-log projection could not be published.\n' >&2
    printf 'Owner-scoped run evidence will continue independently.\n' >&2
  else
    latest_projection_ok=1
  fi
fi

set +e
# Keep the owned command in the caller's foreground process group. The previous
# command | sanitizer | tee topology made the command an asynchronous pipeline
# member, so an interactive SIGINT could stop the shell presentation path without
# reliably cancelling the owned workflow. Only presentation/log persistence is
# asynchronous now; it receives EOF as soon as the foreground command exits.
sanitize_and_persist() {
  if [[ -n "$run_log_fd" ]]; then
    "$logging_python" -m oh_goon.repository_tooling.run_logging --stream "$ROOT" "$base" "$HOME" "$run_log_fd"
  else
    sanitize_stream
  fi
}

{
  printf 'OH-GOON RUN LOG\n'
  printf 'Kind: %s\n' "$label"
  printf 'Started UTC: %s\n' "$timestamp"
  printf 'Working directory: <repo>\n'
  printf 'Command arguments: omitted from diagnostics\n\n'
} | sanitize_and_persist
header_logging_ok=$?

exec {command_log_fd}> >(sanitize_and_persist)
logger_pid=$!
interrupted=0
terminated=0
cancellation_in_progress=0
child_pid=""
owned_group_alive() {
  [[ -n "$child_pid" ]] && kill -0 -- "-$child_pid" 2>/dev/null
}
cancellation_grace() {
  local checks="$1" index
  # Poll with Bash's bounded read timeout over /dev/zero. Unlike one fixed sleep,
  # cooperative children return immediately once their group is gone; /dev/null is
  # forbidden here because EOF would silently collapse every grace period to zero.
  # This keeps cancellation independent of host sleep/coreutils while still allowing
  # nested workflow owners time to reap separately-created child process groups.
  for (( index=0; index<checks; index++ )); do
    owned_group_alive || return 0
    read -r -t 0.05 _ </dev/zero || true
  done
}
cancel_owned_group() {
  local first_signal="$1"
  (( cancellation_in_progress == 0 )) || return 0
  cancellation_in_progress=1
  if owned_group_alive; then
    kill -"$first_signal" -- "-$child_pid" 2>/dev/null || true
    cancellation_grace 50
  fi
  if owned_group_alive; then
    kill -TERM -- "-$child_pid" 2>/dev/null || true
    cancellation_grace 20
  fi
  if owned_group_alive; then
    kill -KILL -- "-$child_pid" 2>/dev/null || true
  fi
}
forward_interrupt() {
  interrupted=1
  cancel_owned_group INT
}
forward_termination() {
  terminated=1
  cancel_owned_group TERM
}
trap forward_interrupt INT
trap forward_termination TERM
# Monitor mode gives the owned command a distinct process group without relying on
# ambient `setsid`. The wrapper can therefore forward operator cancellation to the
# whole immediate command tree while remaining alive long enough to reap it.
set -m
(
  [[ -z "$run_log_fd" ]] || exec {run_log_fd}>&-
  [[ -z "$run_log_dir_fd" ]] || exec {run_log_dir_fd}>&-
  exec "$@"
) >&"$command_log_fd" 2>&1 &
child_pid=$!
wait "$child_pid"
rc=$?
set +m
trap - INT TERM
if (( terminated )); then
  rc=143
elif (( interrupted )); then
  rc=130
fi
exec {command_log_fd}>&-
wait "$logger_pid" 2>/dev/null
command_logging_ok=$?

TZ=UTC printf -v completed_utc '%(%Y%m%dT%H%M%SZ)T' -1
printf -v completed_epoch '%(%s)T' -1
duration_seconds=$((completed_epoch - started_epoch))
{
  printf '\nCompleted UTC: %s\n' "$completed_utc"
  printf 'Duration seconds: %s\n' "$duration_seconds"
  printf 'OH-GOON RUN RESULT: %s (exit %d)\n' "$([ "$rc" -eq 0 ] && printf PASS || printf FAIL)" "$rc"
} | sanitize_and_persist
footer_logging_ok=$?
set -e

persistence_ok=1
[[ -n "$run_log_fd" ]] || persistence_ok=0
(( header_logging_ok == 0 )) || persistence_ok=0
(( command_logging_ok == 0 )) || persistence_ok=0
(( footer_logging_ok == 0 )) || persistence_ok=0

if (( persistence_ok == 1 )); then
  # Only a fully persisted run may acquire the completed .log identity. Keep the
  # active writer outside retention ownership until the logger is drained and the
  # footer is durable, then publish with one atomic rename in the same directory.
  if [[ ! -L "$active_run_log" && -f "$active_run_log" && "$active_run_log" -ef "/proc/self/fd/$run_log_fd" ]] && \
     mv -T "$active_run_log" "/proc/self/fd/$run_log_dir_fd/${active_name%.inprogress}" 2>/dev/null; then
    # Run logs are disposable diagnostics with one bounded retention owner. This is
    # best-effort maintenance only and can never change the wrapped command result.
    if ! (cd "/proc/self/fd/$run_log_dir_fd" && "$logging_python" -m oh_goon.runtime.run_log_retention .) >/dev/null 2>&1; then
      printf '\nWARNING: Oh-Goon run-log retention maintenance failed.\n' >&2
    fi
    if "$logging_python" -m oh_goon.repository_tooling.run_logging --verify-location "$base" "$label" "$run_log_dir_fd"; then
      printf '\nSaved run log: %s\n' "$run_log" | sanitize_stream
      if (( latest_projection_ok == 1 )); then
        printf 'Latest run log: %s\n' "$latest_log" | sanitize_stream
      fi
    else
      printf '\nWARNING: run-log directory was replaced; no current saved pathname can be reported.\n' >&2
    fi
  else
    persistence_ok=0
  fi
fi
if (( persistence_ok == 0 )); then
  printf '\nWARNING: persistent run logging became unavailable during execution.\n' >&2
  printf 'Console output remained live; the logging failure did not change the command result.\n' >&2
fi
[[ -z "$run_log_fd" ]] || exec {run_log_fd}>&-
[[ -z "$run_log_dir_fd" ]] || exec {run_log_dir_fd}>&-
exit "$rc"
