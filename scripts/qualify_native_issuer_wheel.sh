#!/usr/bin/env bash
# M198: explicit independent installed-wheel proof (native WSL / locked host).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ROOT/backend/.venv/bin/python" ]]; then
  printf '%s\n' 'NATIVE INSTALLED ISSUER WHEEL BLOCKED: run make sync to materialize the native backend first' >&2
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'NATIVE INSTALLED ISSUER WHEEL BLOCKED: native uv unavailable' >&2
  exit 2
fi
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
# The standard Hatch wheel requires a genuinely built UI. Its original release
# owner alone publishes generated/release/static; before APP PASS we may only
# stage those frontend bytes inside this disposable *qualification* build root.
# No certification outcome or release artifact is published by this probe.
make --no-print-directory -C "$ROOT" frontend-build
if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  printf '%s\n' 'NATIVE INSTALLED ISSUER WHEEL BLOCKED: frontend build produced no index.html' >&2
  exit 2
fi
mkdir -p "$WORK/source/backend" "$WORK/source/templates" "$WORK/source/generated/release/static"
cp "$ROOT/VERSION" "$WORK/source/VERSION"
cp -a "$ROOT/backend/pyproject.toml" "$ROOT/backend/hatch_build.py" "$WORK/source/backend/"
cp -a "$ROOT/backend/src" "$WORK/source/backend/src"
cp -a "$ROOT/templates/goons" "$ROOT/templates/plans" "$WORK/source/templates/"
cp -a "$ROOT/frontend/dist/." "$WORK/source/generated/release/static/"
# Native Hatch remains the packaging authority and enforces the standard wheel
# UI inclusion rule; this is never an editable/synthetic or bypassed build.
uv run --project "$ROOT/backend" --offline --frozen --no-sync oh-goon-release-wheel-build --offline --root "$WORK/source"
shopt -s nullglob
wheels=("$WORK/source/generated/release/dist/"*.whl)
if [[ ${#wheels[@]} -ne 1 ]]; then
  printf '%s\n' 'NATIVE INSTALLED ISSUER WHEEL BLOCKED: exactly one wheel required' >&2
  exit 2
fi
# M200: the native backend environment was already materialized by `make sync`.
# Installing its dependencies into a second offline venv asks uv to resolve
# uncached registry artifacts even though those exact locked runtime packages
# are present. Install ONLY the candidate wheel into a disposable target;
# execute its entrypoint using native .venv dependencies without ever installing
# the candidate over the source checkout or importing the source package.
uv pip install --offline --no-deps --target "$WORK/wheel-site" "${wheels[0]}"
mkdir "$WORK/isolated"
VERSION="$(cat "$ROOT/VERSION")"
(
  cd "$WORK/isolated"
  unset PYTHONPATH PYTHONHOME
  # Isolated interpreter: wheel target alone is explicitly prepended to sys.path.
  # If an editable source finder wins, RECORD-to-__file__ equality fails closed.
  uv run --no-project --offline --python "$ROOT/backend/.venv/bin/python" \
    python -I -c 'import sys; from pathlib import Path; wheel_site = Path(sys.argv[1]).resolve(strict=True); sys.path.insert(0, str(wheel_site)); from oh_goon.repository_tooling.native_issuer_wheel_probe import main; raise SystemExit(main(["--expected-version", sys.argv[2]]))' \
    "$WORK/wheel-site" "$VERSION"
)
