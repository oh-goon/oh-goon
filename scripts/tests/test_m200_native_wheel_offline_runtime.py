"""M200: offline wheel qualification must reuse native locked dependencies, not re-resolve."""
from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.tests.test_m199_wsl_wheel_qualification import _fixture

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/qualify_native_issuer_wheel.sh"


def test_native_dependency_cache_cannot_be_re_resolved_during_wheel_probe(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    # The stub native uv rejects a second offline runtime resolution. M199's
    # `uv pip install --offline --python disposable-venv <wheel>` failed on WSL
    # despite the dependencies being installed in backend/.venv by `make sync`.
    result = subprocess.run(
        ["bash", str(root / "scripts/qualify_native_issuer_wheel.sh")],
        env=env, cwd=root, text=True, capture_output=True, timeout=12,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'uv pip install --offline --no-deps --target "$WORK/wheel-site"' in source
    assert 'python -I -c ' in source
    assert 'sys.path.insert(0, str(wheel_site))' in source
    assert 'unset PYTHONPATH PYTHONHOME' in source
    assert 'uv venv --offline' not in source
    assert not (root / "generated/release/static/index.html").exists()


def test_missing_native_environment_blocks_before_frontend_or_wheel_build(tmp_path: Path) -> None:
    root, env = _fixture(tmp_path)
    (root / "backend/.venv/bin/python").unlink()
    result = subprocess.run(
        ["bash", str(root / "scripts/qualify_native_issuer_wheel.sh")],
        env=env, cwd=root, text=True, capture_output=True, timeout=12,
    )
    assert result.returncode == 2
    assert "run make sync" in result.stderr
    assert not (root / "frontend/dist/index.html").exists()
