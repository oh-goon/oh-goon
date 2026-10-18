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

sanitize_stream() {
  local line tmp_match state_label="<state>/run-logs"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//"$ROOT"/<repo>}"
    line="${line//"$base"/"$state_label"}"
    line="${line//"$HOME"/<home>}"
    while [[ "$line" =~ /tmp/tmp\.[A-Za-z0-9._-]+ ]]; do
      tmp_match="${BASH_REMATCH[0]}"
      line="${line//"$tmp_match"/<tmp>}"
    done
    printf '%s\n' "$line"
  done
}

sh "$ROOT/scripts/command_banner.sh" "OH-GOON ${label^^}" "Command: $* | Owner: run_logged.sh" | sanitize_stream

if ! mkdir -p "$log_dir" 2>/dev/null; then
  printf 'WARNING: could not create Oh-Goon run-log directory: %s\n' "$log_dir" | sanitize_stream >&2
  echo "Continuing without persistent command logging." >&2
  exec "$@"
fi

# Bash owns wall-clock metadata without an external `date` process. Certification
# sandboxes may deny unrelated executables; logging metadata must never become an
# execution dependency.
TZ=UTC printf -v timestamp '%(%Y%m%dT%H%M%SZ)T' -1
printf -v started_epoch '%(%s)T' -1
run_log="$log_dir/${timestamp}-$$.log"
active_run_log="${run_log}.inprogress"
latest_log="$log_dir/latest.log"
latest_candidate="$log_dir/.latest.${timestamp}.$$.tmp"

# The invocation owns exactly one byte stream: its active run log. Publish
# latest.log as a hard-link projection of that owner-scoped inode, then replace
# the previous projection with one same-directory atomic rename. A later
# invocation can therefore supersede this projection without giving an older
# writer any pathname through which it can mutate the newer run.
latest_projection_ok=1
if ! : >"$active_run_log" 2>/dev/null; then
  latest_projection_ok=0
else
  rm -f "$latest_candidate" 2>/dev/null || true
  if ! ln "$active_run_log" "$latest_candidate" 2>/dev/null || \
     ! mv -fT "$latest_candidate" "$latest_log" 2>/dev/null; then
    latest_projection_ok=0
    rm -f "$latest_candidate" 2>/dev/null || true
    printf '\nWARNING: latest run-log projection could not be published.\n' >&2
    printf 'Owner-scoped run evidence will continue independently.\n' >&2
  fi
fi

set +e
# Keep the owned command in the caller's foreground process group. The previous
# command | sanitizer | tee topology made the command an asynchronous pipeline
# member, so an interactive SIGINT could stop the shell presentation path without
# reliably cancelling the owned workflow. Only presentation/log persistence is
# asynchronous now; it receives EOF as soon as the foreground command exits.
sanitize_and_persist() {
  local line tmp_match state_label="<state>/run-logs" persistence_ok=1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//"$ROOT"/<repo>}"
    line="${line//"$base"/"$state_label"}"
    line="${line//"$HOME"/<home>}"
    while [[ "$line" =~ /tmp/tmp\.[A-Za-z0-9._-]+ ]]; do
      tmp_match="${BASH_REMATCH[0]}"
      line="${line//"$tmp_match"/<tmp>}"
    done
    printf '%s\n' "$line"
    if (( persistence_ok )); then
      if ! printf '%s\n' "$line" >>"$active_run_log" 2>/dev/null; then
        persistence_ok=0
      fi
    fi
  done
  (( persistence_ok == 1 ))
}

{
  printf 'OH-GOON RUN LOG\n'
  printf 'Kind: %s\n' "$label"
  printf 'Started UTC: %s\n' "$timestamp"
  printf 'Working directory: <repo>\n'
  printf 'Command:'
  printf ' %q' "$@"
  printf '\n\n'
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
"$@" >&"$command_log_fd" 2>&1 &
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
(( header_logging_ok == 0 )) || persistence_ok=0
(( command_logging_ok == 0 )) || persistence_ok=0
(( footer_logging_ok == 0 )) || persistence_ok=0

if (( persistence_ok == 1 )); then
  # Only a fully persisted run may acquire the completed .log identity. Keep the
  # active writer outside retention ownership until the logger is drained and the
  # footer is durable, then publish with one atomic rename in the same directory.
  if mv "$active_run_log" "$run_log" 2>/dev/null; then
    # Run logs are disposable diagnostics with one bounded retention owner. This is
    # best-effort maintenance only and can never change the wrapped command result.
    retention_python="$ROOT/backend/.venv/bin/python"
    if [[ -x "$retention_python" ]] && ! "$retention_python" -m oh_goon.runtime.run_log_retention "$log_dir" >/dev/null 2>&1; then
      printf '\nWARNING: Oh-Goon run-log retention maintenance failed.\n' >&2
    fi
    printf '\nSaved run log: %s\n' "$run_log" | sanitize_stream
    if (( latest_projection_ok == 1 )); then
      printf 'Latest run log: %s\n' "$latest_log" | sanitize_stream
    fi
  else
    persistence_ok=0
  fi
fi
if (( persistence_ok == 0 )); then
  printf '\nWARNING: persistent run logging became unavailable during execution.\n' >&2
  printf 'Console output remained live; the logging failure did not change the command result.\n' >&2
fi
exit "$rc"
