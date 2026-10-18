from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init_doctor.sh"


def _run(*, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["sh", str(SCRIPT)], cwd=ROOT, env=merged, text=True, capture_output=True, check=False
    )


def test_doctor_succeeds_on_current_writable_paths_and_leaves_no_probe_bytes(tmp_path: Path) -> None:
    uv_cache = tmp_path / "uv"
    npm_cache = tmp_path / "npm"
    uv_cache.mkdir()
    npm_cache.mkdir()
    result = _run(env={"UV_CACHE_DIR": str(uv_cache), "npm_config_cache": str(npm_cache)})
    assert result.returncode == 0, result.stderr
    assert "OH-GOON INIT DOCTOR" in result.stdout
    assert "diagnostic only" in result.stdout.lower()
    assert "CURRENT PATH PROBES PASS" in result.stdout
    assert "Native uv/npm remain authoritative" in result.stdout
    assert not list(uv_cache.iterdir())
    assert not list(npm_cache.iterdir())


def test_read_only_uv_cache_is_reported_as_diagnostic_not_init_authority() -> None:
    result = _run(env={"UV_CACHE_DIR": "/sys", "npm_config_cache": "/tmp"})
    assert result.returncode == 73
    assert "OH-GOON INIT DOCTOR: STORAGE PROBLEM OBSERVED" in result.stderr
    assert "[FAIL] native uv cache: /sys" in result.stderr
    assert "Diagnostic only" in result.stderr
    assert "bootstrap evidence" in result.stderr
    assert "rerun: make init" not in result.stderr


def test_explicit_uv_cache_identity_is_reported(tmp_path: Path) -> None:
    cache = tmp_path / "selected-uv-cache"
    cache.mkdir()
    result = _run(env={"UV_CACHE_DIR": str(cache), "npm_config_cache": "/tmp"})
    assert result.returncode == 0
    assert f"[OK] native uv cache: {cache}" in result.stdout
