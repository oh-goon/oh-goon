from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.engine.command import RuntimeAuthorityBinding, run_command


def test_sealed_python_entrypoint_retains_native_virtual_environment(tmp_path: Path) -> None:
    entrypoint = tmp_path / "python-entrypoint"
    entrypoint.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.prefix)\n", encoding="utf-8")
    entrypoint.chmod(0o755)
    native = subprocess.run([str(entrypoint)], check=True, capture_output=True, text=True)
    sealed = run_command(str(entrypoint))
    assert sealed.returncode == 0, sealed.stderr
    assert sealed.stdout == native.stdout.rstrip("\n")


def test_run_command_captures_actual_executable_and_path_authority(tmp_path: Path) -> None:
    executable = tmp_path / "authority-tool"
    executable.write_text("#!/bin/sh\nprintf authority-ok", encoding="utf-8")
    executable.chmod(0o755)
    env = {"PATH": str(tmp_path)}

    result = run_command("authority-tool", env=env)

    receipt = result.runtime_authority
    assert receipt is not None
    assert Path(receipt.executable_path) == executable.resolve()
    assert (
        receipt.executable_identity
        == "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()
    )
    assert receipt.executable_byte_count == len(executable.read_bytes())
    assert receipt.path_authority_identity.startswith("sha256:")
    assert receipt.execution_materialization_identity is not None
    assert result.stdout == "authority-ok"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed execution authority")
def test_env_shebang_is_observed_without_false_atomic_materialization(tmp_path: Path) -> None:
    executable = tmp_path / "env-script"
    executable.write_text("#!/usr/bin/env sh\nprintf 'env-script\\n'\n", encoding="utf-8")
    executable.chmod(0o755)

    result = run_command(str(executable))

    assert result.returncode == 0
    assert result.stdout == "env-script"
    assert result.runtime_authority is not None
    assert result.runtime_authority.interpreter_path == str(Path("/usr/bin/env").resolve())
    assert result.runtime_authority.interpreter_identity is not None
    assert result.runtime_authority.execution_materialization_identity is None


def test_run_command_runtime_authority_binding_is_execution_owner_input(tmp_path: Path) -> None:
    executable = tmp_path / "bound-tool"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    def identity(char):
        return "sha256:" + char * 64

    result = run_command(
        "bound-tool",
        env={"PATH": str(tmp_path)},
        runtime_authority=RuntimeAuthorityBinding(
            provider_identity=identity("1"),
            runtime_identity=identity("2"),
            environment_generation_identity=identity("3"),
            dependency_supply_identity=identity("4"),
        ),
    )

    receipt = result.runtime_authority
    assert receipt is not None
    assert receipt.provider_identity == identity("1")
    assert receipt.runtime_identity == identity("2")
    assert receipt.environment_generation_identity == identity("3")
    assert receipt.dependency_supply_identity == identity("4")


def test_runtime_authority_binds_exact_exported_environment() -> None:
    first = run_command(
        sys.executable,
        "-S",
        "-c",
        "pass",
        env={"PATH": os.environ.get("PATH", ""), "OH_GOON_ENV_PROOF": "first"},
    )
    second = run_command(
        sys.executable,
        "-S",
        "-c",
        "pass",
        env={"PATH": os.environ.get("PATH", ""), "OH_GOON_ENV_PROOF": "second"},
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.runtime_authority is not None
    assert second.runtime_authority is not None
    assert first.runtime_authority.environment_identity.startswith("sha256:")
    assert (
        first.runtime_authority.environment_identity
        != second.runtime_authority.environment_identity
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed execution authority")
@pytest.mark.parametrize("mutation", ["replace", "overwrite"])
def test_runtime_authority_executes_admitted_binary_bytes_after_source_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    executable = tmp_path / "admitted-sh"
    shutil.copy2("/bin/sh", executable)
    admitted_bytes = executable.read_bytes()
    admitted_identity = "sha256:" + hashlib.sha256(admitted_bytes).hexdigest()
    replacement = tmp_path / "replacement"
    shutil.copy2("/bin/false", replacement)
    real_popen = subprocess.Popen

    def mutate_before_spawn(*args, **kwargs):
        if mutation == "replace":
            os.replace(replacement, executable)
        else:
            executable.write_bytes(replacement.read_bytes())
            executable.chmod(0o755)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", mutate_before_spawn)
    result = run_command(
        str(executable),
        "-c",
        "printf admitted-binary",
        runtime_authority=RuntimeAuthorityBinding(
            runtime_identity="sha256:" + "1" * 64,
        ),
    )

    assert result.returncode == 0
    assert result.stdout == "admitted-binary"
    assert result.runtime_authority is not None
    assert result.runtime_authority.executable_identity == admitted_identity
    assert result.runtime_authority.executable_byte_count == len(admitted_bytes)
    assert result.runtime_authority.execution_materialization_identity is not None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed execution authority")
def test_runtime_authority_executes_admitted_shebang_interpreter_after_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    interpreter = tmp_path / "admitted-sh"
    shutil.copy2("/bin/sh", interpreter)
    admitted_interpreter_identity = "sha256:" + hashlib.sha256(interpreter.read_bytes()).hexdigest()
    executable = tmp_path / "admitted-script"
    executable.write_text(
        f"#!{interpreter}\nprintf 'admitted-script\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    replacement = tmp_path / "replacement-interpreter"
    shutil.copy2("/bin/false", replacement)
    real_popen = subprocess.Popen

    def replace_interpreter_before_spawn(*args, **kwargs):
        os.replace(replacement, interpreter)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", replace_interpreter_before_spawn)
    result = run_command(
        str(executable),
        runtime_authority=RuntimeAuthorityBinding(
            runtime_identity="sha256:" + "2" * 64,
        ),
    )

    assert result.returncode == 0
    assert result.stdout == "admitted-script"
    assert result.runtime_authority is not None
    assert result.runtime_authority.interpreter_path == str(interpreter.resolve())
    assert result.runtime_authority.interpreter_identity == admitted_interpreter_identity
    assert result.runtime_authority.execution_materialization_identity is not None
