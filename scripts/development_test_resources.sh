#!/usr/bin/env bash
# OH_GOON_FORWARD_ARGV_FROM: 1

oh_goon_test_resolved_cpus() {
  local value=${1-}
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    printf 'TEST CPU CONFIGURATION ERROR: OH_GOON_TEST_CPUS must be an integer from 0 to 4096\n' >&2
    return 2
  fi
  value=${value#"${value%%[!0]*}"}
  value=${value:-0}
  if [[ ${#value} -gt 4 ]] || ((10#$value > 4096)); then
    printf 'TEST CPU CONFIGURATION ERROR: OH_GOON_TEST_CPUS must be an integer from 0 to 4096\n' >&2
    return 2
  fi
  printf '%s\n' "$value"
}

oh_goon_test_is_linux() {
  [[ "$OSTYPE" == linux* ]]
}

oh_goon_test_cgroup_root() {
  printf '/sys/fs/cgroup\n'
}

oh_goon_test_cgroup_directory() {
  local root=$1 hierarchy controllers relative
  while IFS=: read -r hierarchy controllers relative; do
    if [[ "$hierarchy" == 0 && -z "$controllers" && "$relative" == /* ]]; then
      case "/$relative/" in
        */../*|*/./*) return 1 ;;
      esac
      printf '%s%s\n' "$root" "${relative%/}"
      return 0
    fi
  done < /proc/self/cgroup
  return 1
}

oh_goon_test_cgroup_membership_verified() {
  local directory=$1 owner_pid=$2 member
  [[ -r "$directory/cgroup.procs" ]] || return 1
  while IFS= read -r member; do
    [[ "$member" != "$owner_pid" ]] || return 0
  done < "$directory/cgroup.procs"
  return 1
}

oh_goon_test_cpu_budget_verified() {
  local cpus=$1 root=$2 directory=$3 quota period extra
  while :; do
    if [[ -e "$directory/cpu.max" ]]; then
      IFS=' ' read -r quota period extra < "$directory/cpu.max" || return 1
      [[ -z "$extra" && "$period" =~ ^[0-9]+$ && ${#period} -le 7 ]] || return 1
      ((10#$period > 0 && 10#$period <= 1000000)) || return 1
      if [[ "$quota" != max ]]; then
        [[ "$quota" =~ ^[0-9]+$ && ${#quota} -le 18 ]] || return 1
        ((10#$quota > 0)) || return 1
        if ((10#$quota <= cpus * 10#$period)); then
          return 0
        fi
      fi
    fi
    [[ "$directory" != "$root" ]] || break
    directory=${directory%/*}
  done
  return 1
}

oh_goon_test_scope_tasks_max() {
  local root=$1 directory=$2 limit memory_control tasks_max=max
  while [[ "$directory" != "$root" ]]; do
    # This existing app.slice and its ancestors also contain the new user scope.
    [[ ${directory##*/} != app.slice ]] || break
    for memory_control in memory.max memory.high memory.swap.max memory.swap.high memory.zswap.max; do
      if [[ -e "$directory/$memory_control" ]]; then
        if ! IFS= read -r limit < "$directory/$memory_control" || [[ "$limit" != max ]]; then
          printf 'TEST CPU BUDGET UNENFORCED: moving to app.slice cannot preserve the caller %s constraint; keep the existing scope or select OH_GOON_TEST_CPUS=0 explicitly\n' "$memory_control" >&2
          return 1
        fi
      fi
    done
    if [[ -e "$directory/pids.max" ]]; then
      IFS= read -r limit < "$directory/pids.max" || return 1
      if [[ "$limit" != max ]]; then
        [[ "$limit" =~ ^[0-9]+$ && ${#limit} -le 18 ]] || return 1
        ((10#$limit > 0)) || return 1
        if [[ "$tasks_max" == max ]] || ((10#$limit < 10#$tasks_max)); then
          tasks_max=$limit
        fi
      fi
    fi
    directory=${directory%/*}
  done
  printf '%s\n' "$tasks_max"
}

oh_goon_test_tasks_limit_verified() {
  local directory=$1 tasks_max=$2 limit
  [[ "$tasks_max" != max ]] || return 0
  [[ -r "$directory/pids.max" ]] || return 1
  IFS= read -r limit < "$directory/pids.max" || return 1
  [[ "$limit" =~ ^[0-9]+$ && ${#limit} -le 18 ]] || return 1
  ((10#$limit > 0 && 10#$limit <= 10#$tasks_max))
}

oh_goon_test_cpu_controller_available() {
  local root controllers
  root=$(oh_goon_test_cgroup_root) || return 1
  [[ -r "$root/cgroup.controllers" ]] || return 1
  IFS= read -r controllers < "$root/cgroup.controllers" || return 1
  [[ " $controllers " == *" cpu "* ]]
}

oh_goon_test_resources_main() {
  local cpus child=0 script quota root directory owner_pid=$BASHPID tasks_max=max
  local -a tasks_property=() tasks_argument=()
  if [[ ${1-} == --oh-goon-cgroup-child ]]; then
    shift
    cpus=$(oh_goon_test_resolved_cpus "${1-}") || return 2
    if ((cpus == 0)); then
      printf 'TEST CPU CONFIGURATION ERROR: resolved child budget must be positive\n' >&2
      return 2
    fi
    shift
    if [[ ${1-} == --tasks-max ]]; then
      tasks_max=${2-}
      if [[ ! "$tasks_max" =~ ^[0-9]+$ || ${#tasks_max} -gt 18 ]] || ((10#$tasks_max < 1)); then
        printf 'TEST CPU CONFIGURATION ERROR: invalid resolved task limit\n' >&2
        return 2
      fi
      shift 2
    fi
    if [[ $# -lt 2 || ${1-} != -- ]]; then
      printf 'TEST CPU CONFIGURATION ERROR: invalid resolved child invocation\n' >&2
      return 2
    fi
    shift
    child=1
  else
    cpus=$(oh_goon_test_resolved_cpus "${OH_GOON_TEST_CPUS-2}") || return 2
    if [[ ${1-} == -- ]]; then
      shift
    fi
  fi
  if [[ $# -eq 0 ]]; then
    printf 'usage: development_test_resources.sh COMMAND [ARGS...]\n' >&2
    return 2
  fi
  export OH_GOON_TEST_CPUS=$cpus
  if ((cpus == 0)); then
    printf 'Test CPU budget: disabled explicitly (OH_GOON_TEST_CPUS=0)\n' >&2
    exec "$@"
  fi
  if ! oh_goon_test_is_linux; then
    printf 'TEST CPU BUDGET UNENFORCED: Linux cgroup v2 is required; select OH_GOON_TEST_CPUS=0 to opt out explicitly\n' >&2
    return 3
  fi
  if ! root=$(oh_goon_test_cgroup_root) || ! directory=$(oh_goon_test_cgroup_directory "$root"); then
    printf 'TEST CPU BUDGET UNENFORCED: the current cgroup v2 mount mapping is unavailable\n' >&2
    return 3
  fi
  if [[ "$directory" != "$root" && "$directory" != "$root/"* ]] || \
      ! oh_goon_test_cgroup_membership_verified "$directory" "$owner_pid"; then
    printf 'TEST CPU BUDGET UNENFORCED: the mapped cgroup leaf does not prove membership of this process; this mount or namespace layout is unsupported\n' >&2
    return 3
  fi
  if ! oh_goon_test_tasks_limit_verified "$directory" "$tasks_max"; then
    printf 'TEST CPU BUDGET UNENFORCED: the receiving cgroup does not preserve the resolved task limit\n' >&2
    return 3
  fi
  if oh_goon_test_cpu_budget_verified "$cpus" "$root" "$directory"; then
    printf 'Test CPU budget: at most %s CPUs (verified inherited cgroup quota)\n' "$cpus" >&2
    exec "$@"
  fi
  if ((child)); then
    printf 'TEST CPU BUDGET UNENFORCED: native cgroup quota does not establish the resolved %s CPU budget\n' "$cpus" >&2
    return 3
  fi
  if ! tasks_max=$(oh_goon_test_scope_tasks_max "$root" "$directory"); then
    printf 'TEST CPU BUDGET UNENFORCED: caller constraints cannot be preserved in the new native scope\n' >&2
    return 3
  fi
  if ! oh_goon_test_cpu_controller_available; then
    printf 'TEST CPU BUDGET UNENFORCED: readable cgroup v2 CPU controller is unavailable; select OH_GOON_TEST_CPUS=0 to opt out explicitly\n' >&2
    return 3
  fi
  if ! command -v systemd-run >/dev/null 2>&1; then
    printf 'TEST CPU BUDGET UNENFORCED: systemd-run is unavailable; select OH_GOON_TEST_CPUS=0 to opt out explicitly\n' >&2
    return 3
  fi
  script=${BASH_SOURCE[0]}
  [[ "$script" == /* ]] || script="$PWD/$script"
  quota=$((cpus * 100))
  if [[ "$tasks_max" != max ]]; then
    tasks_property=(-p "TasksMax=$tasks_max")
    tasks_argument=(--tasks-max "$tasks_max")
  fi
  printf 'Test CPU budget: native enforcement required; requesting %s CPUs through systemd\n' "$cpus" >&2
  exec systemd-run --user --scope --slice=app.slice \
    -p "CPUQuota=$quota%" -p CPUWeight=20 "${tasks_property[@]}" -- \
    "$BASH" "$script" --oh-goon-cgroup-child "$cpus" "${tasks_argument[@]}" -- "$@"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -u
  oh_goon_test_resources_main "$@"
fi
