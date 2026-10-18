#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/host_python.sh"
PYTHON_BIN="$(select_host_python)"
BASE_MARKER='not certification and not slow and not process and not scale and not host_dns and not dependency_complete'
EXTRA_MARKER="${DIAGNOSTIC_EXTRA_MARKER:-}"
MARKER="$BASE_MARKER"
SURFACE="${DIAGNOSTIC_SURFACE:-all}"
BATCH="${DIAGNOSTIC_BATCH:-1}"
BATCH_LIMIT="${DIAGNOSTIC_BATCH_LIMIT:-1}"
RECEIPT_PATH="${OH_GOON_HOSTED_DIAGNOSTIC_RECEIPT:-}"
# Preserve the diagnostic owner's *original* exit classification across Make,
# which normalizes recipe exit 3 to make exit 2. An exact unavailable capability
# may be optional during normal development; other failures stay failures.
STATUS_PATH="${OH_GOON_HOSTED_DIAGNOSTIC_STATUS:-}"
if [[ -n "$STATUS_PATH" ]]; then
  if [[ -L "$STATUS_PATH" || -d "$STATUS_PATH" ]]; then
    printf '%s\n' "HOSTED DEVELOPMENT DIAGNOSTIC CONFIGURATION ERROR: invalid status path" >&2
    exit 2
  fi
  rm -f -- "$STATUS_PATH" || exit 2
  report_native_diagnostic_exit() {
    local status=$?
    printf '%s\n' "$status" > "$STATUS_PATH" || true
  }
  trap report_native_diagnostic_exit EXIT
fi

if [[ -n "$RECEIPT_PATH" ]]; then
  if [[ -d "$RECEIPT_PATH" && ! -L "$RECEIPT_PATH" ]]; then
    printf '%s\n' "HOSTED DEVELOPMENT DIAGNOSTIC CONFIGURATION ERROR: receipt path is a directory: $RECEIPT_PATH" >&2
    exit 2
  fi
  rm -f -- "$RECEIPT_PATH"
fi

# Additional selection is deliberately a tiny subset of pytest's marker grammar.
# It may select one marker or exclude one marker, which can only intersect the immutable
# safety selector. Accepting arbitrary boolean expressions here would let operator
# precedence or injected parentheses re-enable certification/slow/process/scale/host_dns.
if [[ -n "$EXTRA_MARKER" ]]; then
  if [[ "$EXTRA_MARKER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ || "$EXTRA_MARKER" =~ ^not[[:space:]]+[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    MARKER="$BASE_MARKER and ($EXTRA_MARKER)"
  else
    printf '%s\n' \
      "HOSTED DEVELOPMENT DIAGNOSTIC CONFIGURATION ERROR: DIAGNOSTIC_EXTRA_MARKER must be MARKER or 'not MARKER'; boolean expressions are not accepted" >&2
    exit 2
  fi
fi

surface_config() {
  case "$1" in
    backend-unit) printf '%s\t%s\n' backend/tests/unit 8 ;;
    backend-integration) printf '%s\t%s\n' backend/tests/integration 6 ;;
    repository-tooling) printf '%s\t%s\n' scripts/tests 5 ;;
    *) return 2 ;;
  esac
}

case "$SURFACE" in
  all) surfaces=(backend-unit backend-integration repository-tooling) ;;
  backend-unit|backend-integration|repository-tooling) surfaces=("$SURFACE") ;;
  *)
    printf '%s\n' "HOSTED DEVELOPMENT DIAGNOSTIC CONFIGURATION ERROR: unknown surface '$SURFACE'" >&2
    printf '%s\n' 'Valid surfaces: all, backend-unit, backend-integration, repository-tooling' >&2
    exit 2
    ;;
esac
for value_name in BATCH BATCH_LIMIT; do
  value="${!value_name}"
  case "$value" in
    ''|*[!0-9]*|0)
      printf '%s\n' "HOSTED DEVELOPMENT DIAGNOSTIC CONFIGURATION ERROR: $value_name must be a positive integer" >&2
      exit 2
      ;;
  esac
done

printf '%s\n' \
  'OH-GOON HOSTED DEVELOPMENT DIAGNOSTIC' \
  'Authority: diagnostic only; native uv/.venv remains authoritative for dependency-complete/generated/certification tests' \
  'Policy: use compatible host packages when present; never resolve/install/mutate project dependency state' \
  'Network policy: tests marked host_dns are excluded; constrained hosts never probe external DNS' \
  "Marker policy: $MARKER" \
  "Interpreter: $PYTHON_BIN" \
  "Surface: $SURFACE" \
  "First batch: $BATCH" \
  "Batch limit: $BATCH_LIMIT" \
  'Bound: deterministic file batches; 60s hard timeout each; timeout bisection disabled'

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  printf '%s\n' "HOSTED DEVELOPMENT DIAGNOSTIC BLOCKED: $PYTHON_BIN is unavailable" >&2
  exit 3
}
DIAGNOSTIC_TEMP="$(mktemp -d "${TMPDIR:-/tmp}/oh-goon-hosted-diagnostic.XXXXXX")"
CAPABILITY_PATH="$DIAGNOSTIC_TEMP/host-capability.json"
REPORT_DIR="$DIAGNOSTIC_TEMP/reports"
REPORT_MANIFEST="$DIAGNOSTIC_TEMP/report-manifest.tsv"
mkdir -p "$REPORT_DIR"
: > "$REPORT_MANIFEST"
cleanup_diagnostic_and_report_status() {
  local status=$?
  rm -rf -- "$DIAGNOSTIC_TEMP"
  if [[ -n "$STATUS_PATH" ]]; then
    printf '%s\n' "$status" > "$STATUS_PATH" || true
  fi
}
trap cleanup_diagnostic_and_report_status EXIT

# A host package can satisfy a constrained diagnostic even when the selected native uv cache
# does not contain the artifact needed to materialize the isolated native project environment. Validate that distinction
# from repository-owned dependency intent before any tests run.  In particular,
# Every declared Python dependency is inventoried and version-checked from
# pyproject.toml. Import capability is probed without consulting uv cache state.
# Cryptography additionally exercises its product-facing Ed25519/signature,
# PEM-serialization, and X.509 surfaces so stale metadata cannot hide a broken
# native/OpenSSL binding.
PYTHONNOUSERSITE=1 \
  "$PYTHON_BIN" -I "$ROOT/backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py" \
  --output "$CAPABILITY_PATH"


cd "$ROOT"
# Deliberately replace rather than extend ambient PYTHONPATH.  Otherwise the
# test process could import a different dependency than the isolated capability
# probe just certified, invalidating the host-capability receipt.
export PYTHONPATH="$ROOT/backend/src"
export PYTHONNOUSERSITE=1
# Host pytest installations may expose unrelated entry-point plugins (for example
# telemetry plugins) that are not part of Oh-Goon's dependency authority.  Keep
# the diagnostic hermetic; the batch runner still loads repository-owned plugins
# explicitly with -p when a receipt is required.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

for surface in "${surfaces[@]}"; do
  IFS=$'\t' read -r path batch_size < <(surface_config "$surface")
  for ((offset = 0; offset < BATCH_LIMIT; offset++)); do
    batch=$((BATCH + offset))
    printf '\n=== HOSTED DIAGNOSTIC: %s batch %d ===\n' "$surface" "$batch"
    report_path="$REPORT_DIR/${surface}-${batch}.json"
    "$PYTHON_BIN" -P -m oh_goon.repository_tooling.test_batches "$path" \
      --granularity files \
      --batch-size "$batch_size" \
      --workers 1 \
      --batch-timeout 60 \
      --batch "$batch" \
      --no-timeout-bisection \
      --marker "$MARKER" \
      --report-path "$report_path"
    printf '%s\t%s\t%s\n' "$surface" "$batch" "$report_path" >> "$REPORT_MANIFEST"
  done
done

if [[ -n "$RECEIPT_PATH" ]]; then
  "$PYTHON_BIN" -P -m oh_goon.repository_tooling.hosted_diagnostic_receipt \
    --output "$RECEIPT_PATH" \
    --manifest "$REPORT_MANIFEST" \
    --host-capability "$CAPABILITY_PATH" \
    --surface "$SURFACE" \
    --first-batch "$BATCH" \
    --batch-limit "$BATCH_LIMIT" \
    --base-marker "$BASE_MARKER" \
    --extra-marker "$EXTRA_MARKER" \
    --effective-marker "$MARKER"
fi
