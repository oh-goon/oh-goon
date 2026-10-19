"""M199: the WSL wheel probe needs a real UI, never a pre-certified release artifact."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/qualify_native_issuer_wheel.sh"


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "candidate"
    for path in (
        "scripts",
        "backend/.venv/bin",
        "backend/src/oh_goon/native_issuer",
        "frontend",
        "templates/goons",
        "templates/plans",
    ):
        (root / path).mkdir(parents=True, exist_ok=True)
    (root / "scripts/qualify_native_issuer_wheel.sh").write_bytes(SCRIPT.read_bytes())
    (root / "VERSION").write_text("1267.0.1052\n", encoding="utf-8")
    (root / "backend/pyproject.toml").write_text(
        "[project]\nname='oh-goon'\nversion='1267.0.1052'\n"
    )
    (root / "backend/hatch_build.py").write_text("# hatch owner\n")
    (root / "backend/src/oh_goon/native_issuer/__init__.py").write_text(
        "# issuer owner\n"
    )
    (root / "backend/.venv/bin/python").write_text("#!/bin/sh\nexit 0\n")
    (root / "backend/.venv/bin/python").chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make = bin_dir / "make"
    make.write_text(
        "#!/bin/bash\nset -eu\n"
        "[[ \"$*\" == *'frontend-build' ]] || exit 70\n"
        'mkdir -p "$OH_GOON_WHEEL_FIXTURE_ROOT/frontend/dist"\n'
        'printf "%s\\n" "<!doctype html><title>native-frontend</title>" > '
        '"$OH_GOON_WHEEL_FIXTURE_ROOT/frontend/dist/index.html"\n',
        encoding="utf-8",
    )
    make.chmod(0o755)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'printf "%s\\n" "$1" >> "$OH_GOON_WHEEL_FIXTURE_ROOT/uv-calls.log"\n'
        'case "$1" in\n'
        "  build)\n"
        '    [[ "$*" == *"--offline --wheel"* ]] || exit 71\n'
        '    src="$4"; out="$6"\n'
        '    [[ -f "$(dirname "$src")/generated/release/static/index.html" ]] || exit 72\n'
        '    [[ -f "$src/src/oh_goon/native_issuer/__init__.py" ]] || exit 73\n'
        '    [[ ! -e "$OH_GOON_WHEEL_FIXTURE_ROOT/generated/release/static/index.html" ]] || exit 74\n'
        '    mkdir -p "$out"\n'
        '    touch "$out/oh_goon-1267.0.1052-py3-none-any.whl"\n'
        "    ;;\n"
        "  pip)\n"
        '    [[ "$*" == *"--offline --no-deps --target"* ]] || exit 76\n'
        '    [[ "${OH_GOON_WHEEL_FIXTURE_INSTALL_BLOCK:-0}" == "0" ]] || exit 78\n'
        "    ;;\n"
        "  run)\n"
        '    [[ "$*" == *"python -I -c"* ]] || exit 77\n'
        "    ;;\n"
        "  *) exit 75 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["OH_GOON_WHEEL_FIXTURE_ROOT"] = str(root)
    return root, env


def test_wheel_qualification_stages_real_frontend_in_disposable_context_only(
    tmp_path: Path,
) -> None:
    root, env = _fixture(tmp_path)
    result = subprocess.run(
        ["bash", str(root / "scripts/qualify_native_issuer_wheel.sh")],
        env=env,
        cwd=root,
        text=True,
        capture_output=True,
        timeout=12,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert (root / "frontend/dist/index.html").is_file()
    assert not (root / "generated/release/static/index.html").exists()


def test_qualification_never_weakens_release_hook_or_creates_app_pass(
    tmp_path: Path,
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    hook = (ROOT / "backend/hatch_build.py").read_text(encoding="utf-8")
    assert 'make --no-print-directory -C "$ROOT" frontend-build' in source
    assert 'uv build --offline --wheel "$WORK/source/backend"' in source
    assert (
        'cp -a "$ROOT/frontend/dist/." "$WORK/source/generated/release/static/"'
        in source
    )
    assert '"$ROOT/generated/release/static' not in source
    assert 'version != "standard"' in hook
    assert "standard release wheel requires generated/release/static/index.html" in hook


def test_failed_candidate_install_cannot_reach_installed_issuer_probe(
    tmp_path: Path,
) -> None:
    """Fail before positive evidence if the candidate wheel cannot be installed."""
    root, env = _fixture(tmp_path)
    env["OH_GOON_WHEEL_FIXTURE_INSTALL_BLOCK"] = "1"
    result = subprocess.run(
        ["bash", str(root / "scripts/qualify_native_issuer_wheel.sh")],
        env=env,
        cwd=root,
        text=True,
        capture_output=True,
        timeout=12,
    )
    assert result.returncode != 0
    invoked = (root / "uv-calls.log").read_text(encoding="utf-8").splitlines()
    assert invoked == ["build", "pip"]
    assert not (root / "generated/release/static/index.html").exists()
