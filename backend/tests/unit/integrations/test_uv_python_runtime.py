from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from oh_goon.integrations import uv_python_runtime


def test_find_uv_system_python_returns_canonical_owned_runtime_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "uv"
    executable.write_bytes(b"uv")
    system_python = tmp_path / "python3.13-real"
    system_python.write_bytes(b"python")
    alias = tmp_path / "python3.13"
    alias.symlink_to(system_python.name)
    seen: dict[str, object] = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=f"{alias}\n", stderr="")

    monkeypatch.setattr(uv_python_runtime.subprocess, "run", run)

    assert uv_python_runtime.find_uv_system_python(executable) == system_python
    assert seen["argv"] == (str(executable), "python", "find", "--system")
    assert seen["env"]["PATH"] == uv_python_runtime.os.defpath
    assert seen["env"]["UV_NO_MANAGED_PYTHON"] == "1"
