#!/usr/bin/env bash
# B12: audited, bounded, auditable DEVELOPMENT witness. Never certifies issuer, LACE or release.
# Usage: make qualify-development-wsl [QUALIFY_EVIDENCE_DIR=../...] [QUALIFY_SOURCE_MANIFEST=/trusted/file]
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"
usage() {
  printf '%s\n' 'Usage: bash scripts/qualify_b12_development_wsl.sh [EVIDENCE_DIRECTORY] [EXTERNAL_SOURCE_MANIFEST]' \
    '  Default: ../OH_GOON_B12_NATIVE_EVIDENCE (automatically uses a new numbered path)' \
    '  --snapshot: inspect current source inventory, with no WSL requirement or writes' \
    '  --audit: report known extraction sidecars, unsafe entries and source membership' \
    '  QUALIFY_STRESS_REPEATS=10: run ten independent native process/race repetitions (no retries)' \
    '  No external manifest is needed for DEVELOPMENT. Changed code is recorded, not trusted.' \
    '  Providing an external manifest opts into strict exact-source checking.'
}
if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then usage; exit 0; fi
if (( $# > 2 )); then usage >&2; exit 64; fi
for command in sha256sum realpath cmp timeout uv seq; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'BLOCKED: missing development qualification tool: %s\n' "$command" >&2
    exit 3
  fi
done
# Use the same generated-directory exclusions on both PRE and POST. Never hash
# dependency or pytest caches; all authored files, both locks included.
source_inventory() {
  PYTHONPATH=backend/src uv run --no-project python -m \
    oh_goon.repository_tooling.b12_transport_audit inventory --root "$root"
}
source_transport_audit() {
  PYTHONPATH=backend/src uv run --no-project python -m \
    oh_goon.repository_tooling.b12_transport_audit audit --root "$root" "$@"
}

if [[ "${1:-}" == '--snapshot' ]]; then
  source_inventory
  exit 0
fi
if [[ "${1:-}" == '--audit' ]]; then
  source_transport_audit
  exit $?
fi
# B27 effectful witness stays opt-in in the ORIGINAL qualification ring.
# Missing bwrap is BLOCKED before environment work: never silently skip a
# requested kernel mount test or transfer an earlier source qualification.
if [[ "${QUALIFY_B27_EFFECTFUL:-0}" != 0 && "${QUALIFY_B27_EFFECTFUL:-0}" != 1 ]]; then
  printf '%s\n' 'BLOCKED: QUALIFY_B27_EFFECTFUL must be 0 or 1' >&2
  exit 3
fi
if [[ "${QUALIFY_B27_EFFECTFUL:-0}" == 1 ]] && ! command -v bwrap >/dev/null 2>&1; then
  printf '%s\n' 'BLOCKED: effectful B27 witness requires real bwrap' >&2
  exit 3
fi
# Do not create evidence or execute project code when the host is not WSL.
if ! grep -Eiq 'microsoft|wsl' /proc/sys/kernel/osrelease /proc/version 2>/dev/null; then
  printf '%s\n' 'BLOCKED: native WSL is required; use normal development tests on other Linux hosts' >&2
  exit 3
fi
if [[ ! -f backend/uv.lock || ! -f frontend/package-lock.json || ! -f backend/pyproject.toml ]]; then
  printf '%s\n' 'BLOCKED: authored backend/frontend dependency locks missing' >&2
  exit 3
fi
base="$(realpath -m -- "${1:-../OH_GOON_B12_NATIVE_EVIDENCE}")"
if [[ "$base" == "$root" || "$base" == "$root/"* || ! -d "$(dirname "$base")" ]]; then
  printf '%s\n' 'BLOCKED: evidence must be in an existing sibling/external directory' >&2
  exit 3
fi
# An explicitly requested external manifest is strict: reject before making any
# evidence directory, never silently fall back to working-tree mode.
external="${2:-}"
if [[ -n "$external" ]]; then
  # An external trusted manifest must cover every authored file, not only a
  # subset whose listed checksums happen to match.
  if [[ -L "$external" || ! -f "$external" ]] \
      || ! sha256sum --status --check "$external" \
      || ! cmp -s "$external" <(source_inventory); then
    printf '%s\n' 'BLOCKED: externally pinned source manifest does not match' >&2
    exit 3
  fi
fi
# Packaged-source exact-member gate prevents unknown extra artifacts and code
# substitution while allowing known Windows ADS sidecars. Working-tree development
# may explicitly opt out but never gains any release/certification authority.
embedded='.checkpoint/B11_SOURCE.sha256'
if [[ -f .checkpoint/B11_SOURCE.sha256 ]]; then embedded='.checkpoint/B11_SOURCE.sha256'; fi
if [[ -f .checkpoint/B12_SOURCE.sha256 ]]; then embedded='.checkpoint/B12_SOURCE.sha256'; fi
if [[ -f .checkpoint/B13_SOURCE.sha256 ]]; then embedded='.checkpoint/B13_SOURCE.sha256'; fi
if [[ -f .checkpoint/B14_SOURCE.sha256 ]]; then embedded='.checkpoint/B14_SOURCE.sha256'; fi
if [[ -f .checkpoint/B15_SOURCE.sha256 ]]; then embedded='.checkpoint/B15_SOURCE.sha256'; fi
if [[ -f .checkpoint/B16_SOURCE.sha256 ]]; then embedded='.checkpoint/B16_SOURCE.sha256'; fi
if [[ -f .checkpoint/B17_SOURCE.sha256 ]]; then embedded='.checkpoint/B17_SOURCE.sha256'; fi
if [[ -f .checkpoint/B18_SOURCE.sha256 ]]; then embedded='.checkpoint/B18_SOURCE.sha256'; fi
if [[ -f .checkpoint/B19_SOURCE.sha256 ]]; then embedded='.checkpoint/B19_SOURCE.sha256'; fi
if [[ -f .checkpoint/B20_SOURCE.sha256 ]]; then embedded='.checkpoint/B20_SOURCE.sha256'; fi
if [[ -f .checkpoint/B21_SOURCE.sha256 ]]; then embedded='.checkpoint/B21_SOURCE.sha256'; fi
if [[ -f .checkpoint/B22_SOURCE.sha256 ]]; then embedded='.checkpoint/B22_SOURCE.sha256'; fi
if [[ -f .checkpoint/B23_SOURCE.sha256 ]]; then embedded='.checkpoint/B23_SOURCE.sha256'; fi
if [[ -f .checkpoint/UX_A_SOURCE.sha256 ]]; then embedded='.checkpoint/UX_A_SOURCE.sha256'; fi
if [[ -f .checkpoint/UX_B_SOURCE.sha256 ]]; then embedded='.checkpoint/UX_B_SOURCE.sha256'; fi
if [[ -f .checkpoint/UX_C_SOURCE.sha256 ]]; then embedded='.checkpoint/UX_C_SOURCE.sha256'; fi
if [[ -f .checkpoint/UX_D_SOURCE.sha256 ]]; then embedded='.checkpoint/UX_D_SOURCE.sha256'; fi
if [[ -f .checkpoint/B24_SOURCE.sha256 ]]; then embedded='.checkpoint/B24_SOURCE.sha256'; fi
if [[ -f .checkpoint/B25_SOURCE.sha256 ]]; then embedded='.checkpoint/B25_SOURCE.sha256'; fi
if [[ -f .checkpoint/B26_SOURCE.sha256 ]]; then embedded='.checkpoint/B26_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG00_SOURCE.sha256 ]]; then embedded='.checkpoint/AG00_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG01_SOURCE.sha256 ]]; then embedded='.checkpoint/AG01_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02A_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02A_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B1_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B2_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B2_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3A_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3A_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3B1_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3B2_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B2_SOURCE.sha256'; fi
if [[ -f .checkpoint/QENV1_SOURCE.sha256 ]]; then embedded='.checkpoint/QENV1_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3B3A_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B3A_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3B3B1_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B3B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3B3B2_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B3B2_SOURCE.sha256'; fi
if [[ -f .checkpoint/AG02B2B3B3B3_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B3B3_SOURCE.sha256'; fi
if [[ -f .checkpoint/B3B3B4_SOURCE.sha256 ]]; then embedded='.checkpoint/B3B3B4_SOURCE.sha256'; fi
if [[ -f .checkpoint/T0A_SOURCE.sha256 ]]; then embedded='.checkpoint/T0A_SOURCE.sha256'; fi
if [[ -f .checkpoint/T0B1_SOURCE.sha256 ]]; then embedded='.checkpoint/T0B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/T0B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/T0B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/T0B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/T0B2B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/T0B2B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/T0B2B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B1A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B1A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B2B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B2B2_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B2B2_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B2B3A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B2B3A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27B2B3B2B3B_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B3B2B3B_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2A1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2A1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2B2B_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2B2B_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2B2B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2B2B2_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2B2B2_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P1P2B2B3_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P1P2B2B3_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P3A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P3A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P3B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P3B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P3B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P3B2A_SOURCE.sha256'; fi
if [[ -f .checkpoint/P1B27_GATE_P3B2B_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27_GATE_P3B2B_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB0DB1A_SOURCE.sha256 ]]; then embedded='.checkpoint/DB0DB1A_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB1B1_SOURCE.sha256 ]]; then embedded='.checkpoint/DB1B1_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB1B2_SOURCE.sha256 ]]; then embedded='.checkpoint/DB1B2_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB2_SOURCE.sha256 ]]; then embedded='.checkpoint/DB2_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB3_SOURCE.sha256 ]]; then embedded='.checkpoint/DB3_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB4_SOURCE.sha256 ]]; then embedded='.checkpoint/DB4_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB5_SOURCE.sha256 ]]; then embedded='.checkpoint/DB5_SOURCE.sha256'; fi
if [[ -f .checkpoint/DB6_SOURCE.sha256 ]]; then embedded='.checkpoint/DB6_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R01_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R01_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R2W1_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R2W1_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R2W2_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R2W2_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R2W3_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R2W3_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R2C_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R2C_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R2C_Q1_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R2C_Q1_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3A_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3A_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3B_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3B_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3C_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3C_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3D_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3D_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3E_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3E_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3F_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3F_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3G_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3G_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3H_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3H_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3I_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3I_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3J_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3J_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3K_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3K_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3L_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3L_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3M_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3M_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3N_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3N_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3O_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3O_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3P_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3P_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3Q_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3Q_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3R_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3R_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3S_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3S_SOURCE.sha256'; fi
if [[ -f .checkpoint/CO_R3T_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3T_SOURCE.sha256'; fi
# Current exact-source development gate; this manifest excludes itself.
if [[ -f .checkpoint/CO_R3U2B2B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3U2B2B2B1_SOURCE.sha256'; fi
if [[ -f "$embedded" ]] && [[ "${QUALIFY_ALLOW_WORKING_SOURCE:-0}" != 1 ]]; then
  if ! source_transport_audit --baseline "$embedded" >/dev/null; then
    printf '%s\n' 'BLOCKED: candidate source differs from exact packaged manifest (set QUALIFY_ALLOW_WORKING_SOURCE=1 only for development)' >&2
    exit 3
  fi
fi
if [[ ! "${QUALIFY_STRESS_REPEATS:-1}" =~ ^[1-9][0-9]*$ ]] \
    || (( ${QUALIFY_STRESS_REPEATS:-1} > 20 )); then
  printf 'BLOCKED: QUALIFY_STRESS_REPEATS must be 1..20\n' >&2
  exit 3
fi
# No overwrite, no manual cleanup needed. The directory is reserved atomically.
evidence=''
for attempt in $(seq 1 999); do
  if (( attempt == 1 )); then candidate="$base"; else printf -v suffix -- '-run-%03d' "$attempt"; candidate="${base}${suffix}"; fi
  if [[ -L "$candidate" ]]; then
    printf '%s\n' 'BLOCKED: symlinked evidence destination' >&2
    exit 3
  fi
  if mkdir -- "$candidate" 2>/dev/null; then evidence="$candidate"; break; fi
done
if [[ -z "$evidence" ]]; then printf '%s\n' 'BLOCKED: no unused evidence directory' >&2; exit 3; fi
printf 'EVIDENCE_DIR=%s\n' "$evidence"
phase='ADMISSION'
last_result=3
source_before=''
source_after=''
source_integrity='NOT_CHECKED'
# Once admitted, always leave a POST even when pytest, uv, timeout or signals fail.
finish() {
  local exitcode=$?
  trap - EXIT INT TERM
  set +e
  source_transport_audit > "$evidence/TRANSPORT_POST.json"
  transport_rc=$?
  source_inventory > "$evidence/POST_SOURCE.sha256"
  capture_rc=$?
  if (( transport_rc != 0 )) || ! cmp -s "$evidence/TRANSPORT_PRE.json" "$evidence/TRANSPORT_POST.json"; then
    source_integrity=FAIL
    exitcode=3
  fi
  if (( capture_rc == 0 && transport_rc == 0 )) && cmp -s "$evidence/PRE_SOURCE.sha256" "$evidence/POST_SOURCE.sha256"; then
    source_integrity=PASS
  else
    source_integrity=FAIL
    exitcode=3
  fi
  source_before="$(sha256sum "$evidence/PRE_SOURCE.sha256" | cut -d' ' -f1)"
  source_after="$(sha256sum "$evidence/POST_SOURCE.sha256" | cut -d' ' -f1)"
  {
    printf 'schema=oh-goon.development-wsl-witness/v3\n'
    printf 'exit_code=%s\n' "$exitcode"
    printf 'source_integrity=%s\n' "$source_integrity"
    printf 'source_before_sha256=%s\nsource_after_sha256=%s\n' "$source_before" "$source_after"
    printf 'classification=DEVELOPMENT_ONLY\nexternal_issuer=NOT_PROVEN\nphysical_lace=NOT_PROVEN\n'
    printf 'gate_a=BLOCKED\ngate_b=BLOCKED\napp_pass=NOT_CLAIMED\nrelease_authority=NONE\n'
  } > "$evidence/POST.txt"
  sha256sum "$evidence/"*.txt "$evidence/"*.log "$evidence/"*.xml "$evidence/"*.json 2>/dev/null > "$evidence/LOG_SHA256.txt"
  printf 'EVIDENCE_DIR=%s\nRESULT=%s\n' "$evidence" "$exitcode"
  exit "$exitcode"
}
trap finish EXIT
if ! source_transport_audit > "$evidence/TRANSPORT_PRE.json" \
    || ! source_inventory > "$evidence/PRE_SOURCE.sha256"; then
  printf '%s\n' 'BLOCKED: source inventory rejected' >&2
  exit 3
fi
# External manifest remains an *opt-in strict* authority. Embedded self-hashes
# are a convenience integrity check only; they cannot authenticate a release.
binding='WORKING_SOURCE_NOT_EXTERNALLY_ATTESTED'
if [[ -n "$external" ]]; then
  binding='EXTERNAL_MANIFEST_MATCHED_NOT_RELEASE_ATTESTED'
elif [[ -f "$embedded" ]]; then
  # Self-documenting development manifest excludes its own file to avoid
  # circular hashing. Still compare the FULL authored-source inventory.
  if sha256sum --status --check "$embedded" \
      && cmp -s "$embedded" <(source_inventory | grep -v -F "  ./$embedded"); then
    binding='EMBEDDED_MANIFEST_MATCHED_UNTRUSTED_DEVELOPMENT'
  else
    binding='WORKING_SOURCE_DIFFERS_FROM_EMBEDDED_MANIFEST'
    printf '%s\n' 'INFO: working source differs from packaged checkpoint; development tests still allowed' >&2
  fi
fi
{
  printf 'schema=oh-goon.development-wsl-witness/v3\n'
  printf 'classification=DEVELOPMENT_ONLY\n'
  printf 'source_binding=%s\n' "$binding"
  printf 'external_issuer=NOT_PROVEN\nindependent_root_custody=NOT_PROVEN\nphysical_lace=NOT_PROVEN\n'
  printf 'gate_a=BLOCKED\ngate_b=BLOCKED\napp_pass=NOT_CLAIMED\nrelease_authority=NONE\n'
  printf 'wsl_kernel=%s\n' "$(cat /proc/sys/kernel/osrelease)"
  printf 'source_snapshot_sha256=%s\n' "$(sha256sum "$evidence/PRE_SOURCE.sha256" | cut -d' ' -f1)"
} > "$evidence/PRE.txt"
# CO-R3U2B2B2B1: pre-environment admission. The CO-R3N positive witness predates the original LACE
# non-issuing physical gate. An explicitly requested old positive run must
# fail BEFORE uv sync / Python environment preparation / any native ring: it cannot legitimately execute and its
# historical green receipt must never be mistaken for current kernel custody.
# The actual Linux *refusal* witness belongs to the SAME original CORE ring.
r3u2b2b2a_refusal_tests=()
if [[ -f .checkpoint/CO_R3U2B2B2A_SOURCE.sha256 ]]; then
  r3u2b2b2a_refusal_tests+=(tests/native/test_co_r3u2b2b2a_native_refusal.py)
  if [[ ${QUALIFY_R3N_CGROUP_ROOT:-} != "" ]]; then
    printf 'status=BLOCKED_NON_ISSUING_LACE\nreason=legacy_positive_native_witness_incompatible_with_current_physical_gate\nphysical_lace=NOT_PROVEN\n' > "$evidence/R3N_NATIVE_CGROUP_STATUS.txt"
    printf '%s\n' 'BLOCKED: historical CO-R3N positive tests cannot exercise unprovisioned current LACE; no native effects attempted' >&2
    phase=BLOCKED_UNPROVISIONED_KERNEL_CUSTODY
    printf 'blocked_before_environment=YES\nlegacy_native_positive=REFUSED\n' > "$evidence/R3U2B2B2B1_ADMISSION_STATUS.txt"
    last_result=3
    exit 3
  fi
fi
# Host-owned diagnostics only. A killed launcher may never enter the Python
# supervisor, so collect bounded kernel memory facts OUTSIDE that process.
# These samples are development observations; they are never LACE facts.
qualify_host_sample() {
  local name="$1" cg=''
  cg="$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup 2>/dev/null || true)"
  {
    printf 'schema=oh-goon.qualify-host-sample/v1\nclassification=DEVELOPMENT_DIAGNOSTIC_ONLY\n'
    date -u '+utc=%Y-%m-%dT%H:%M:%SZ'
    grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo 2>/dev/null || true
    if [[ -r /proc/pressure/memory ]]; then cat /proc/pressure/memory; fi
    for base in /sys/fs/cgroup "/sys/fs/cgroup${cg}"; do
      [[ -d "$base" ]] || continue
      for key in memory.max memory.current memory.swap.max memory.swap.current memory.events; do
        if [[ -r "$base/$key" ]]; then
          printf '[%s/%s]\n' "$base" "$key"
          cat "$base/$key"
        fi
      done
    done
  } > "$evidence/${name}_HOST.txt"
}

# Canonical qualification environment transaction: PREPARE ONCE -> VERIFY ONCE.
# Do not install, repair or resolve dependencies inside an isolated ring.
qualification_python="$root/backend/.venv/bin/python"
qualify_host_sample 'UV_SYNC_BEFORE'
printf 'RUNNING=UV_LOCKED_SYNC\n'
uv_sync_rc=0
(cd backend && timeout --signal=TERM --kill-after=10s 210s \
  uv sync --locked --group dev) > "$evidence/UV_LOCKED_SYNC.log" 2>&1 || uv_sync_rc=$?
qualify_host_sample 'UV_SYNC_AFTER'
printf 'UV_LOCKED_SYNC=%s\n' "$uv_sync_rc" >> "$evidence/RING_RESULTS.txt"
if (( uv_sync_rc != 0 )); then
  printf 'INFRA_BLOCKED=UV_LOCKED_SYNC exit=%s; see UV_LOCKED_SYNC.log and *_HOST.txt\n' "$uv_sync_rc" \
    | tee "$evidence/INFRA_FAILURE.txt" >&2
  exit 1
fi

# uv --check is explicitly non-mutating. Confirm the locked group is fully
# synchronized before the first test process; offline verification cannot
# download an "emergency" wheel or mutate the lock. Runtime identity and
# architecture compliance are verified in this same one-time phase.
printf 'RUNNING=UV_ENV_VERIFY\n'
uv_verify_rc=0
(cd backend && timeout --signal=TERM --kill-after=10s 90s \
  uv sync --check --locked --offline --group dev) > "$evidence/UV_ENV_VERIFY.log" 2>&1 || uv_verify_rc=$?
if (( uv_verify_rc == 0 )) && [[ -x "$qualification_python" ]]; then
  OH_GOON_QUALIFICATION_ENV="$root/backend/.venv" \
    "$qualification_python" -c 'import os, pathlib, sys; expected = pathlib.Path(os.environ["OH_GOON_QUALIFICATION_ENV"]).resolve(); assert pathlib.Path(sys.prefix).resolve() == expected, "qualification interpreter outside prepared env"; print("python=" + sys.version.split()[0]); print("prefix=" + str(expected))' \
    >> "$evidence/UV_ENV_VERIFY.log" 2>&1 || uv_verify_rc=$?
else
  if (( uv_verify_rc == 0 )); then uv_verify_rc=3; fi
fi
# Architecture is an existing project-wide gate. Its new environment contract
# rejects attempts to reintroduce in-ring setup or a second environment owner.
if (( uv_verify_rc == 0 )); then
  (cd backend && "$qualification_python" -m oh_goon.repository_tooling.check_architecture) \
    > "$evidence/QUALIFIER_ARCHITECTURE.log" 2>&1 || uv_verify_rc=$?
fi
printf 'UV_ENV_VERIFY=%s\n' "$uv_verify_rc" >> "$evidence/RING_RESULTS.txt"
if (( uv_verify_rc != 0 )); then
  printf 'INFRA_BLOCKED=UV_ENV_VERIFY exit=%s; see UV_ENV_VERIFY.log and QUALIFIER_ARCHITECTURE.log\n' "$uv_verify_rc" \
    | tee "$evidence/INFRA_FAILURE.txt" >&2
  exit 1
fi
{
  printf 'schema=oh-goon.qualification-prepared-environment/v1\n'
  printf 'preparation_count=1\nverification_count=1\n'
  printf 'rings_require_project_sync=NO\n'
  printf 'interpreter=%s\n' "$qualification_python"
  printf 'pyproject_sha256=%s\n' "$(sha256sum backend/pyproject.toml | cut -d' ' -f1)"
  printf 'lock_sha256=%s\n' "$(sha256sum backend/uv.lock | cut -d' ' -f1)"
  printf 'architecture=PASS\nclassification=DEVELOPMENT_ONLY\n'
} > "$evidence/UV_ENV_VERIFIED.txt"

# The ring is supervised by one project-locked Python entrypoint. A pytest
# worker runs in its OWN Linux process group. Even a green JUnit is rejected
# on nonzero exit, missing/invalid XML, deadline, or surviving descendants.
run_ring() {
  local name="$1"; shift
  printf 'RUNNING=%s\n' "$name"
  local rc=0
  local -a required_modules=()
  if [[ "$name" == CORE && -f .checkpoint/CO_R3O_SOURCE.sha256 && -n "${QUALIFY_R3N_CGROUP_ROOT:-}" ]]; then
    required_modules+=(--require-module test_co_r3n_native_cgroup_v2_witness)
  fi
  if [[ "$name" == PHYSICAL_OBSERVER* ]]; then
    if [[ "${QUALIFY_B27_EFFECTFUL:-0}" == 1 ]]; then
      required_modules+=(--require-module test_p1b27b2b3b2b3a_effectful_mount)
    fi
    for module in test_b13_local_physical_preflight test_b15_cgroup_subtree_evidence \
      test_b16_tracked_namespace_evidence test_b17_namespace_fd_holders \
      test_b18_foreign_mount_targets test_b19_visible_mount_fd_holders \
      test_b20_linux_proc_observer_witness; do
      required_modules+=(--require-module "$module")
    done
    if [[ -f .checkpoint/B21_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_b21_shared_observer_collapse)
    fi
    if [[ -f .checkpoint/B22_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_b22_original_physical_sampling)
    fi
    if [[ -f .checkpoint/B23_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_b23_signed_mount_intent_sampling)
    fi
    if [[ -f .checkpoint/B24_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_b24_original_resource_envelope)
    fi
    if [[ -f .checkpoint/B25_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_b25_shared_snapshot_parity)
    fi
    if [[ -f .checkpoint/B26_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_b26_original_native_roster)
    fi
    if [[ -f .checkpoint/P1B27B1_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b1_native_mount_roster)
    fi
    if [[ -f .checkpoint/P1B27B2A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2a_signed_native_sources)
    fi
    if [[ -f .checkpoint/P1B27B2B2A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b2a_pinned_bind_source)
    fi
    if [[ -f .checkpoint/P1B27B2B3A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b3a_kernel_mount_coverage)
    fi
    if [[ -f .checkpoint/P1B27B2B3B1_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b3b1_mount_fd_lifecycle)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b3b2a_original_mount_fd_correlation)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2B1_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b3b2b1_holder_race_fences)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2B2_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b3b2b2_native_fd_handoff)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2B3B_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b3b2b3b_fd_argv_boundary)
    fi
    if [[ -f .checkpoint/P1B27_GATE_P1P2A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27_gate_p1p2_original_sample_correspondence)
      if [[ -f .checkpoint/P1B27_GATE_P1P2A1_SOURCE.sha256 ]]; then
        required_modules+=(--require-module test_p1b27_gate_p1p2a1_native_seal_fixture)
        if [[ -f .checkpoint/P1B27_GATE_P1P2B1_SOURCE.sha256 ]]; then
          required_modules+=(--require-module test_p1b27_gate_p1p2b1_original_causal_mount_ledger)
          if [[ -f .checkpoint/P1B27_GATE_P1P2B2A_SOURCE.sha256 ]]; then
            required_modules+=(--require-module test_p1b27_gate_p1p2b2a_native_kernel_provenance)
          fi
          if [[ -f .checkpoint/P1B27_GATE_P1P2B2B_SOURCE.sha256 ]]; then
            required_modules+=(--require-module test_p1b27_gate_p1p2b2b_original_journal_sample)
          fi
          if [[ -f .checkpoint/P1B27_GATE_P1P2B2B1_SOURCE.sha256 ]]; then
            required_modules+=(--require-module test_p1b27_gate_p1p2b2b1_mount_fixture)
            if [[ -f .checkpoint/P1B27_GATE_P1P2B2B2_SOURCE.sha256 ]]; then
              required_modules+=(--require-module test_p1b27_gate_p1p2b2b2_canonical_root)
              if [[ -f .checkpoint/P1B27_GATE_P1P2B2B3_SOURCE.sha256 ]]; then
                required_modules+=(--require-module test_p1p2b2b3_original_holder_claim_consistency)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3A_SOURCE.sha256 ]]; then
                required_modules+=(--require-module test_p3a_original_sample_rounds)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3B1_SOURCE.sha256 ]]; then
                required_modules+=(--require-module test_p3b1_original_convergence_assessment)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3B2A_SOURCE.sha256 ]]; then
                required_modules+=(--require-module test_p3b2a_complete_postcleanup_roster)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3B2B_SOURCE.sha256 ]]; then
                required_modules+=(--require-module test_p3b2b_original_native_lifecycle)
              fi
            fi
          fi
        fi
      fi
    fi
  fi
  if [[ "$name" == AGENT_PREWORK* ]]; then
    required_modules+=(--require-module test_ag01_agent_authoring_source_closure)
    required_modules+=(--require-module test_ag02_frozen_agent_source_capture)
    required_modules+=(--require-module test_ag02b_typed_agent_prework_handoff)
    required_modules+=(--require-module test_ag02b2b1_original_agent_source_reservation)
    required_modules+=(--require-module test_ag02b2b2_original_owned_attempt_source)
    required_modules+=(--require-module test_ag02b2b3_original_agent_attempt_staging)
    required_modules+=(--require-module test_ag02b2b3b_readonly_source_custody)
    required_modules+=(--require-module test_ag02b2b3b2_original_mount_intent_preflight)
    required_modules+=(--require-module test_ag00_canonical_agent_architecture)
    required_modules+=(--require-module test_ag02b2b3b3b1_original_intent_agent_fence)
    required_modules+=(--require-module test_ag02b2b3b3b2_signed_mount_consumption)
    required_modules+=(--require-module test_ag02b2b3b3b3_signed_stage_mount)
    required_modules+=(--require-module test_b3b3b2_native_manifest_precedence)
    required_modules+=(--require-module test_b3b3b4_workspace_signed_mount)
    required_modules+=(--require-module test_t0a_trust_authority_registry)
    required_modules+=(--require-module test_t0b_trust_custody_review)
    required_modules+=(--require-module test_t0b2_offline_custody_review --require-module test_t0b2b1_custody_dossier)
    if [[ -f .checkpoint/T0B2B2A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_t0b2b2a_native_host_observation)
    fi
    if [[ -f .checkpoint/P1B27A_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27a_signed_host_tool_custody)
    fi
    if [[ -f .checkpoint/P1B27B2B1_SOURCE.sha256 ]]; then
      required_modules+=(--require-module test_p1b27b2b1_kernel_fd_custody)
    fi
    required_modules+=(--require-module test_ag02b2b3b3b1_execution_order_architecture)
  fi
  qualify_host_sample "${name}_BEFORE"
  (cd backend && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONNOUSERSITE=1 \
    UV_NO_SYNC=1 UV_OFFLINE=1 PIP_NO_INDEX=1 \
    timeout --signal=TERM --kill-after=10s 210s \
    "$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor \
    --name "$name" --evidence "$evidence" --timeout 180 "${required_modules[@]}" -- "$@") \
    > "$evidence/${name}_LAUNCH.log" 2>&1 || rc=$?
  qualify_host_sample "${name}_AFTER"
  local missing_receipt=0
  if [[ ! -s "$evidence/${name}.json" ]]; then
    printf 'BLOCKED: missing authoritative process receipt: %s\n' "$name" \
      >> "$evidence/${name}_LAUNCH.log"
    missing_receipt=1
  fi
  # Retain the actual launch exit (137 SIGKILL, 124 outer timeout, etc.).
  # A test-level failure with a complete signed-supervisor-style receipt may
  # still run remaining independent rings; *infrastructure* loss must stop
  # immediately and preserve the POST source/transport evidence via EXIT trap.
  printf '%s=%s missing_receipt=%s\n' "$name" "$rc" "$missing_receipt" \
    >> "$evidence/RING_RESULTS.txt"
  if (( rc != 0 || missing_receipt != 0 )); then last_result=1; fi
  if (( rc == 137 || rc == 124 || rc == 143 || missing_receipt != 0 )); then
    printf 'INFRA_BLOCKED=%s exit=%s missing_receipt=%s; inspect %s_LAUNCH.log, %s_*_HOST.txt and kernel OOM counters\n' \
      "$name" "$rc" "$missing_receipt" "$name" "$name" \
      | tee "$evidence/INFRA_FAILURE.txt" >&2
    exit 1
  fi
}
last_result=0
# DB6 native kernel fault evidence is opt-in on a caller-provisioned small,
# private, disposable mount. It is part of the ORIGINAL CORE qualifier, not
# a second executable/qualifier; unset means native kernel ENOSPC NOT PROVEN.
db6_native_tests=()
if [[ -f .checkpoint/DB6_SOURCE.sha256 && -n "${QUALIFY_DB6_ENOSPC_ROOT:-}" ]]; then
  db6_native_tests+=(tests/native/test_db6_kernel_enospc_witness.py)
fi
# CO-R3N real cgroup-v2 evidence is opt-in inside the ORIGINAL CORE ring.
# No root means NOT_PROVEN, never a simulated pass. Explicit request means
# a strict real-kernel test: failure is qualification failure, no skip/fallback.
r3n_native_tests=()
if [[ -f .checkpoint/CO_R3N_SOURCE.sha256 ]]; then
  if [[ -n "${QUALIFY_R3N_CGROUP_ROOT:-}" ]]; then
    r3n_native_tests+=(tests/native/test_co_r3n_native_cgroup_v2_witness.py)
    printf 'status=REQUESTED_REAL_KERNEL\nroot=%s\n' "$QUALIFY_R3N_CGROUP_ROOT" > "$evidence/R3N_NATIVE_CGROUP_STATUS.txt"
    if [[ -f .checkpoint/CO_R3O_SOURCE.sha256 ]]; then
      # Enforce non-skipped native-module identity in the ORIGINAL supervisor.
      # An interrupted ring leaves REQUESTED, never a manufactured PASS.
      printf 'status=REQUESTED_UNQUALIFIED\n' > "$evidence/R3O_NATIVE_STATUS.txt"
    fi
  else
    printf 'status=NOT_PROVEN\nreason=no_disposable_delegated_cgroup_root\n' > "$evidence/R3N_NATIVE_CGROUP_STATUS.txt"
    if [[ -f .checkpoint/CO_R3O_SOURCE.sha256 ]]; then
      printf 'status=NOT_PROVEN\nreason=no_disposable_delegated_cgroup_root\n' > "$evidence/R3O_NATIVE_STATUS.txt"
    fi
  fi
fi
# Fail-before-work for untrusted or malformed extraction metadata. Sidecars
# remain on disk; the non-authoritative receipt records every excluded byte.
run_ring CORE \
  tests/unit/runtime/test_b12d2c3b4_authenticated_takeover_preflight.py \
  tests/unit/runtime/test_b12d2c3b5_atomic_restart_transfer.py \
  tests/unit/runtime/test_b12d2c3b7_scheduler_reclaim_fence.py \
  tests/unit/runtime/test_b12d2c3b8_original_issuer_restart_fence.py \
  tests/unit/runtime/test_b12d2c3b9_independent_recovery_root_custody.py \
  tests/unit/services/test_group_a_atomic_fleet_prework_ingress.py \
  tests/unit/services/test_group_a_signer_trust_epoch.py \
  tests/unit/runtime/test_db0db1a_canonical_database_owner.py \
  tests/unit/repository_tooling/test_db0db1a_database_owner_architecture.py \
  tests/unit/services/test_db0db1a_atomic_fleet_owner_cut.py \
  tests/unit/runtime/test_db1b1_atomic_domain_operations.py \
  tests/unit/runtime/test_db1b2_transaction_owner_and_replay.py \
  tests/unit/runtime/test_db2_sqlite_admission_and_faults.py \
  tests/unit/runtime/test_db3_scout_backup_owner.py \
  tests/unit/runtime/test_db4_offline_recovery_certification.py \
  tests/unit/runtime/test_db5_backup_publication_failures.py \
  tests/unit/runtime/test_db6_native_fault_preflight.py \
  tests/unit/repository_tooling/test_db6_native_fault_architecture.py \
  tests/unit/repository_tooling/test_co_r01_lifecycle_architecture.py \
  tests/unit/repository_tooling/test_co_r01_roadmap_architecture.py \
  tests/unit/runtime/test_co_r2b_workspace_recovery.py \
  tests/unit/runtime/test_co_r2w2_native_git_recovery.py \
  tests/unit/repository_tooling/test_co_r2w2_owner_architecture.py \
  tests/unit/runtime/test_co_r2w3_git_retirement.py \
  tests/unit/repository_tooling/test_co_r2c_q1_isolated_core_collection.py \
  tests/unit/repository_tooling/test_co_r2w3_owner_architecture.py \
  tests/unit/runtime/test_co_r2c_evidence_acceptance_cut.py \
  tests/unit/runtime/test_co_r3_native_provider_conformance.py \
  tests/unit/runtime/test_co_r3b_original_failure_cuts.py \
  tests/unit/runtime/test_co_r3c_trust_writer_quiescence.py \
  tests/unit/integrations/test_co_r3d_original_cgroup_quiescence.py \
  tests/unit/repository_tooling/test_co_r3d_original_cgroup_architecture.py \
  tests/unit/integrations/test_co_r3e_durable_cgroup_recovery.py \
  tests/unit/integrations/test_co_r3f_cgroup_native_handle.py \
  tests/unit/repository_tooling/test_co_r3f_handle_architecture.py \
  tests/unit/integrations/test_co_r3g_bound_observation_removal.py \
  tests/unit/repository_tooling/test_co_r3g_bound_observation_architecture.py \
  tests/unit/integrations/test_co_r3h_original_inode_retirement.py \
  tests/unit/repository_tooling/test_co_r3h_inode_retirement_architecture.py \
  tests/unit/integrations/test_co_r3i_original_lace_cgroup_custody.py \
  tests/unit/repository_tooling/test_co_r3i_original_lace_custody_architecture.py \
  tests/unit/integrations/test_co_r3j_original_parent_lineage.py \
  tests/unit/repository_tooling/test_co_r3j_parent_lineage_architecture.py \
  tests/unit/integrations/test_co_r3k_prework_identity_rollback.py \
  tests/unit/repository_tooling/test_co_r3k_prework_architecture.py \
  tests/unit/integrations/test_co_r3l_bound_nested_attachment.py \
  tests/unit/integrations/test_co_r3m_pending_cgroup_allocation.py \
  tests/unit/repository_tooling/test_co_r3m_pending_allocation_architecture.py \
  tests/unit/repository_tooling/test_co_r3n_native_cgroup_qualification.py \
  tests/unit/repository_tooling/test_co_r3o_native_witness_contract.py \
  tests/unit/services/test_co_r3p_original_fleet_effect_revocation.py \
  tests/unit/services/test_co_r3q_effect_revalidation.py \
  tests/unit/repository_tooling/test_co_r3q_effect_architecture.py \
  tests/unit/repository_tooling/test_co_r3r_visible_holder_race.py \
  tests/unit/repository_tooling/test_co_r3r_observer_architecture.py \
  tests/native/test_co_r3r_real_visible_holder.py \
  tests/unit/repository_tooling/test_co_r3s_pinned_cgroup_observation.py \
  tests/unit/repository_tooling/test_co_r3s_architecture.py \
  tests/unit/integrations/test_co_r3t_legacy_orphan_fail_before_effect.py \
  tests/unit/repository_tooling/test_co_r3t_legacy_custody_architecture.py \
  tests/unit/repository_tooling/test_co_r3p_original_effect_epoch_architecture.py \
  tests/unit/repository_tooling/test_co_r3l_bound_attachment_architecture.py \
  tests/unit/repository_tooling/test_co_r3e_original_recovery_architecture.py \
  tests/unit/repository_tooling/test_co_r3c_original_quiescence_architecture.py \
  tests/unit/repository_tooling/test_co_r3b_original_failure_architecture.py \
  tests/unit/repository_tooling/test_co_r3_native_provider_architecture.py \
  tests/unit/repository_tooling/test_co_r2c_acceptance_architecture.py \
  tests/unit/engine/test_commands.py \
  tests/unit/repository_tooling/test_db3_backup_owner_architecture.py \
  tests/unit/repository_tooling/test_db4_offline_recovery_architecture.py \
  tests/unit/repository_tooling/test_db1b2_database_owner_architecture.py \
  "${r3u2b2b2a_refusal_tests[@]}" \
  "${r3n_native_tests[@]}" \
  "${db6_native_tests[@]}"
# Do not promote REQUESTED into evidence of a successfully completed native
# witness. CORE is the ONE supervised authority for all selected tests.
if [[ -f .checkpoint/CO_R3O_SOURCE.sha256 && -n "${QUALIFY_R3N_CGROUP_ROOT:-}" ]]; then
  if (( last_result == 0 )) && [[ -s "$evidence/CORE.json" ]]; then
    printf 'status=CORE_ACCEPTED_NATIVE_DEVELOPMENT_ONLY\nphysical_lace=NOT_PROVEN\ntrust_revocation=NOT_PROVEN\n' > "$evidence/R3O_NATIVE_STATUS.txt"
  else
    printf 'status=FAILED_OR_INCOMPLETE\nphysical_lace=NOT_PROVEN\n' > "$evidence/R3O_NATIVE_STATUS.txt"
  fi
fi
# AG-02B1 and AG-02B2A share one development-only prework ring. It must run on the exact
# source and cannot substitute for original Fleet admission or native LACE.
if [[ -f .checkpoint/AG02B1_SOURCE.sha256 ]]; then
  run_ring AGENT_PREWORK \
    tests/unit/services/test_ag01_agent_authoring_source_closure.py \
    tests/unit/services/test_ag02_frozen_agent_source_capture.py \
    tests/unit/services/test_ag02b_typed_agent_prework_handoff.py \
    tests/unit/runtime/test_ag02b2b1_original_agent_source_reservation.py \
    tests/unit/services/test_ag02b2b2_original_owned_attempt_source.py \
    tests/unit/runtime/test_ag02b2b3_original_agent_attempt_staging.py \
    tests/unit/runtime/test_ag02b2b3b_readonly_source_custody.py \
    tests/unit/services/test_ag02b2b3b2_original_mount_intent_preflight.py \
    tests/unit/runtime/test_ag02b2b3b3a_agent_mount_custody_gate.py \
    tests/unit/runtime/test_ag02b2b3b3b1_original_intent_agent_fence.py \
    tests/unit/integrations/test_ag02b2b3b3b2_signed_mount_consumption.py \
    tests/unit/services/test_ag02b2b3b3b3_signed_stage_mount.py \
    tests/unit/runtime/test_b3b3b2_native_manifest_precedence.py \
    tests/unit/runtime/test_b3b3b4_workspace_signed_mount.py \
    tests/unit/runtime/test_p1b27a_signed_host_tool_custody.py \
    tests/unit/repository_tooling/test_t0a_trust_authority_registry.py \
    tests/unit/repository_tooling/test_t0b_trust_custody_review.py \
    tests/unit/repository_tooling/test_t0b2_offline_custody_review.py tests/unit/repository_tooling/test_t0b2b1_custody_dossier.py \
    tests/unit/repository_tooling/test_t0b2b2a_native_host_observation.py \
    tests/unit/runtime/test_ag02b2b3b3b1_execution_order_architecture.py \
    tests/unit/runtime/test_p1b27b2b1_kernel_fd_custody.py \
    tests/unit/repository_tooling/test_ag00_canonical_agent_architecture.py
fi
# Explicit stress is observation, never a hidden retry. Any failed attempt
# makes the full witness fail even when a later repetition passes.
repeats="${QUALIFY_STRESS_REPEATS:-1}"
if [[ ! "$repeats" =~ ^[1-9][0-9]*$ ]] || (( repeats > 20 )); then
  printf 'BLOCKED: QUALIFY_STRESS_REPEATS must be 1..20\n' >&2
  exit 3
fi
for (( iteration=1; iteration<=repeats; iteration++ )); do
  printf -v suffix '_%02d' "$iteration"
  run_ring "B6_PROCESS${suffix}" tests/integration/test_b12d2c3b6_native_restart_witness.py -k 'not interrupted_sqlite_transfer_without_commit'
  run_ring "B6_ROLLBACK${suffix}" tests/integration/test_b12d2c3b6_native_restart_witness.py -k 'interrupted_sqlite_transfer_without_commit'
  run_ring "B7_RACES${suffix}" tests/integration/test_b12d2c3b7_scheduler_recovery_races.py
  if [[ -f .checkpoint/B20_SOURCE.sha256 ]]; then
    # First-class, independently supervised Linux /proc and kernel-observer
    # regression ring. Selected by exact filenames, never pytest discovery.
    # All six modules MUST contribute a non-skipped JUnit testcase.
    observer_tests=()
    if [[ -f .checkpoint/B21_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/repository_tooling/test_b21_shared_observer_collapse.py)
    fi
    if [[ -f .checkpoint/B22_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_b22_original_physical_sampling.py)
    fi
    if [[ -f .checkpoint/B23_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_b23_signed_mount_intent_sampling.py)
    fi
    if [[ -f .checkpoint/B24_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_b24_original_resource_envelope.py)
    fi
    if [[ -f .checkpoint/B25_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_b25_shared_snapshot_parity.py)
    fi
    if [[ -f .checkpoint/B26_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_b26_original_native_roster.py)
    fi
    if [[ -f .checkpoint/P1B27A_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27a_signed_host_tool_custody.py)
    fi
    if [[ -f .checkpoint/P1B27B1_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b1_native_mount_roster.py)
    fi
    if [[ -f .checkpoint/P1B27B2A_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2a_signed_native_sources.py)
    fi
    if [[ -f .checkpoint/P1B27B2B1_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b1_kernel_fd_custody.py)
    fi
    if [[ -f .checkpoint/P1B27B2B2A_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b2a_pinned_bind_source.py)
    fi
    if [[ -f .checkpoint/P1B27B2B3A_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b3a_kernel_mount_coverage.py)
    fi
    if [[ -f .checkpoint/P1B27B2B3B1_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b3b1_mount_fd_lifecycle.py)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2A_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b3b2a_original_mount_fd_correlation.py)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2B1_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b3b2b1_holder_race_fences.py)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2B2_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b3b2b2_native_fd_handoff.py)
    fi
    if [[ -f .checkpoint/P1B27B2B3B2B3B_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27b2b3b2b3b_fd_argv_boundary.py)
    fi
    if [[ -f .checkpoint/P1B27_GATE_P1P2A_SOURCE.sha256 ]]; then
      observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2_original_sample_correspondence.py)
      if [[ -f .checkpoint/P1B27_GATE_P1P2A1_SOURCE.sha256 ]]; then
        observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2a1_native_seal_fixture.py)
        if [[ -f .checkpoint/P1B27_GATE_P1P2B1_SOURCE.sha256 ]]; then
          observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2b1_original_causal_mount_ledger.py)
          if [[ -f .checkpoint/P1B27_GATE_P1P2B2A_SOURCE.sha256 ]]; then
            observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2b2a_native_kernel_provenance.py)
          fi
          if [[ -f .checkpoint/P1B27_GATE_P1P2B2B_SOURCE.sha256 ]]; then
            observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2b2b_original_journal_sample.py)
          fi
          if [[ -f .checkpoint/P1B27_GATE_P1P2B2B1_SOURCE.sha256 ]]; then
            observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2b2b1_mount_fixture.py)
            if [[ -f .checkpoint/P1B27_GATE_P1P2B2B2_SOURCE.sha256 ]]; then
              observer_tests+=(tests/unit/runtime/test_p1b27_gate_p1p2b2b2_canonical_root.py)
              if [[ -f .checkpoint/P1B27_GATE_P1P2B2B3_SOURCE.sha256 ]]; then
                observer_tests+=(tests/unit/runtime/test_p1p2b2b3_original_holder_claim_consistency.py)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3A_SOURCE.sha256 ]]; then
                observer_tests+=(tests/unit/runtime/test_p3a_original_sample_rounds.py)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3B1_SOURCE.sha256 ]]; then
                observer_tests+=(tests/unit/runtime/test_p3b1_original_convergence_assessment.py)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3B2A_SOURCE.sha256 ]]; then
                observer_tests+=(tests/unit/runtime/test_p3b2a_complete_postcleanup_roster.py)
              fi
              if [[ -f .checkpoint/P1B27_GATE_P3B2B_SOURCE.sha256 ]]; then
                observer_tests+=(tests/unit/runtime/test_p3b2b_original_native_lifecycle.py)
              fi
            fi
          fi
        fi
      fi
    fi
    if [[ "${QUALIFY_B27_EFFECTFUL:-0}" == 1 ]]; then
      observer_tests+=(tests/native/test_p1b27b2b3b2b3a_effectful_mount.py)
    fi
    run_ring "PHYSICAL_OBSERVER${suffix}" \
      "${observer_tests[@]}" \
      tests/unit/repository_tooling/test_b13_local_physical_preflight.py \
      tests/unit/repository_tooling/test_b15_cgroup_subtree_evidence.py \
      tests/unit/repository_tooling/test_b16_tracked_namespace_evidence.py \
      tests/unit/repository_tooling/test_b17_namespace_fd_holders.py \
      tests/unit/repository_tooling/test_b18_foreign_mount_targets.py \
      tests/unit/repository_tooling/test_b19_visible_mount_fd_holders.py \
      tests/unit/repository_tooling/test_b20_linux_proc_observer_witness.py
  fi
done
exit "$last_result"
