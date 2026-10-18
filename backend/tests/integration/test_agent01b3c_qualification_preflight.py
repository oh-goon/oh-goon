"""Native qualification must never return a false PASS for missing dependencies."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.integration import test_agent01b3c_native_model_review as native


@pytest.mark.parametrize("selected", [False, True])
def test_native_model_absence_is_not_a_qualification_pass(monkeypatch, selected):
    # The injected PATH contains no `ollama`, regardless of hosted tools.
    original_which = native.shutil.which
    monkeypatch.setattr(native.shutil, "which", lambda exe: None if exe == "ollama" else original_which(exe))
    monkeypatch.setenv("OH_GOON_AGENT_MODEL", "qwen3:0.6b")
    if selected:
        monkeypatch.setenv(native.REQUIRED_ENV, "1")
        with pytest.raises(pytest.fail.Exception, match="real ollama executable unavailable"):
            native._require_real_native_dependencies()
    else:
        monkeypatch.delenv(native.REQUIRED_ENV, raising=False)
        with pytest.raises(pytest.skip.Exception, match="real ollama executable unavailable"):
            native._require_real_native_dependencies()


def test_native_script_missing_ollama_fails_before_dependency_materialization(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts" / "qualify_agent_local_model.sh"
    # Even in a hosted environment that has no Ollama, it is a runnable native
    # entrypoint, and refusal must precede network/uv dependency operations.
    if os.name != "posix" or native.shutil.which("ollama") is not None:
        pytest.skip("requires Linux host without Ollama; no simulated binary")
    completed = subprocess.run(
        ["bash", str(script), "qwen3:0.6b"], capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 2
    assert "UNQUALIFIED: required native executable missing: ollama" in completed.stderr
    assert "Materializing authoritative locked backend dependencies" not in completed.stdout
