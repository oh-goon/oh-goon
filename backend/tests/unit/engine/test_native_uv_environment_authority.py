from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from oh_goon.engine.command import CommandResult
from oh_goon.engine.fleet_extensions import _check_environment as check_extension_environment
from oh_goon.engine.fleet_extensions import _sync_environment as sync_extension_environment
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.native_uv_environment import capture_native_uv_authority
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _native_environment() -> dict[str, str]:
    return {
        "HOME": "/home/operator",
        "USERPROFILE": "/home/operator",
        "XDG_CONFIG_HOME": "/home/operator/.config",
        "PATH": "/home/operator/.local/bin:/usr/bin",
        "UV_CONFIG_FILE": "/secure/native-uv.toml",
        "UV_DEFAULT_INDEX": "https://packages.example/simple",
        "UV_INDEX": "private=https://private.example/simple",
        "UV_INDEX_PRIVATE_USERNAME": "service-account",
        "UV_INDEX_PRIVATE_PASSWORD": "credential-secret",
        "UV_FUTURE_NATIVE_SETTING": "opaque-native-value",
        "HTTPS_PROXY": "http://proxy.example:8443",
        "SSL_CERT_FILE": "/etc/ssl/certs/corporate.pem",
        "UNRELATED_SECRET": "must-not-reach-native-uv",
    }


def test_native_uv_authority_is_opaque_and_only_fleet_controls_override_it(tmp_path: Path) -> None:
    from oh_goon.core.path_layouts import ManagedPreparationPathLayout

    native = capture_native_uv_authority(_native_environment())
    preparation = ManagedPreparationPathLayout.from_root(tmp_path / "preparation")
    env = native.environment(
        preparation,
        environment=tmp_path / "environment",
        cache=tmp_path / "cache",
    )

    assert env["HOME"] == "/home/operator"
    assert env["XDG_CONFIG_HOME"] == "/home/operator/.config"
    assert env["PATH"] == "/home/operator/.local/bin:/usr/bin"
    assert env["UV_CONFIG_FILE"] == "/secure/native-uv.toml"
    assert env["UV_DEFAULT_INDEX"] == "https://packages.example/simple"
    assert env["UV_INDEX_PRIVATE_PASSWORD"] == "credential-secret"
    assert env["UV_FUTURE_NATIVE_SETTING"] == "opaque-native-value"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8443"
    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/corporate.pem"
    assert env["UV_CACHE_DIR"] == str(tmp_path / "cache")
    assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / "environment")
    assert "UNRELATED_SECRET" not in env
    assert "credential-secret" not in repr(native)


def test_native_uv_subprocess_helper_uses_operator_executable_discovery_authority(
    tmp_path: Path,
) -> None:
    from oh_goon.core.path_layouts import ManagedPreparationPathLayout

    helpers = tmp_path / "helpers"
    helpers.mkdir()
    keyring = helpers / "keyring"
    keyring.write_text("#!/bin/sh\nprintf 'credential-from-helper\\n'\n", encoding="utf-8")
    keyring.chmod(0o755)
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'test "$UV_KEYRING_PROVIDER" = subprocess\n'
        "keyring get https://private.example service-account\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)

    native = capture_native_uv_authority(
        {
            "PATH": str(helpers),
            "UV_KEYRING_PROVIDER": "subprocess",
            "UNRELATED_SECRET": "must-not-reach-native-uv",
        }
    )
    preparation = ManagedPreparationPathLayout.from_root(tmp_path / "preparation")
    env = native.environment(preparation, environment=tmp_path / "environment")

    result = subprocess.run(
        (str(uv),),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "credential-from-helper\n"
    assert env["PATH"] == str(helpers)
    assert env["UV_KEYRING_PROVIDER"] == "subprocess"
    assert "UNRELATED_SECRET" not in env


def test_native_uv_authority_uses_platform_default_path_only_when_operator_path_is_absent(
    tmp_path: Path,
) -> None:
    from oh_goon.core.path_layouts import ManagedPreparationPathLayout

    native = capture_native_uv_authority({"UV_FUTURE_NATIVE_SETTING": "opaque"})
    preparation = ManagedPreparationPathLayout.from_root(tmp_path / "preparation")

    env = native.environment(preparation, environment=tmp_path / "environment")

    assert env["PATH"] == os.defpath


def test_pre_commit_lock_check_and_sync_share_one_captured_native_uv_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _native_environment().items():
        monkeypatch.setenv(name, value)

    store = FleetToolStore(tmp_path / "plan-tools", path_authority=TEST_PATH_AUTHORITY)
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def fake_run_command(*argv: str, **kwargs: object) -> CommandResult:
        env = cast(dict[str, str], kwargs["env"])
        calls.append((tuple(argv), dict(env)))
        project = Path(argv[argv.index("--project") + 1])
        if argv[1] == "lock":
            (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            # Force the same preparation operation through check -> sync after lock.
            environment = Path(env["UV_PROJECT_ENVIRONMENT"])
            environment.mkdir(parents=True, exist_ok=True)
            return CommandResult(returncode=0, stdout="", stderr="", argv=tuple(argv), duration_ms=1)
        if "--check" in argv:
            return CommandResult(returncode=1, stdout="", stderr="stale", argv=tuple(argv), duration_ms=1)
        environment = Path(env["UV_PROJECT_ENVIRONMENT"])
        executable = environment / ("Scripts" if os.name == "nt" else "bin") / (
            "pre-commit.exe" if os.name == "nt" else "pre-commit"
        )
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("", encoding="utf-8")
        return CommandResult(returncode=0, stdout="", stderr="", argv=tuple(argv), duration_ms=1)

    monkeypatch.setattr("oh_goon.engine.fleet_tools.run_command", fake_run_command)
    store.prepare_pre_commit("pre-commit==4.6.2")

    assert [argv[1] for argv, _env in calls] == ["lock", "sync", "sync"]
    assert "--check" in calls[1][0]
    for _argv, env in calls:
        assert env["UV_CONFIG_FILE"] == "/secure/native-uv.toml"
        assert env["UV_INDEX_PRIVATE_PASSWORD"] == "credential-secret"
        assert env["UV_FUTURE_NATIVE_SETTING"] == "opaque-native-value"
        assert env["HOME"] == "/home/operator"
        assert env["PATH"] == "/home/operator/.local/bin:/usr/bin"
        assert "UNRELATED_SECRET" not in env
    transported = {
        key: calls[0][1][key]
        for key in (
            "HOME",
            "XDG_CONFIG_HOME",
            "PATH",
            "UV_CONFIG_FILE",
            "UV_DEFAULT_INDEX",
            "UV_INDEX",
            "UV_INDEX_PRIVATE_PASSWORD",
            "UV_FUTURE_NATIVE_SETTING",
            "HTTPS_PROXY",
            "SSL_CERT_FILE",
        )
    }
    assert all(
        {key: env[key] for key in transported} == transported for _argv, env in calls[1:]
    )


def test_extension_uv_check_and_sync_consume_same_native_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.core.path_layouts import ManagedPreparationPathLayout
    import oh_goon.engine.fleet_extensions as extensions

    native = capture_native_uv_authority(_native_environment())
    preparation = ManagedPreparationPathLayout.from_root(tmp_path / "extensions")
    project = tmp_path / "project"
    environment = tmp_path / "environment"
    project.mkdir()
    calls: list[dict[str, str]] = []

    def fake_run_command(*argv: str, **kwargs: object) -> CommandResult:
        calls.append(dict(cast(dict[str, str], kwargs["env"])))
        return CommandResult(returncode=0, stdout="", stderr="", argv=tuple(argv), duration_ms=1)

    monkeypatch.setattr(extensions, "run_command", fake_run_command)
    check_extension_environment(
        project,
        environment,
        TEST_PATH_AUTHORITY,
        preparation,
        native,
        timeout=1.0,
    )
    sync_extension_environment(project, environment, TEST_PATH_AUTHORITY, preparation, native)

    assert len(calls) == 2
    for env in calls:
        assert env["UV_CONFIG_FILE"] == "/secure/native-uv.toml"
        assert env["UV_INDEX_PRIVATE_PASSWORD"] == "credential-secret"
        assert env["UV_FUTURE_NATIVE_SETTING"] == "opaque-native-value"
        assert env["HOME"] == "/home/operator"
        assert env["PATH"] == "/home/operator/.local/bin:/usr/bin"
        assert "UNRELATED_SECRET" not in env
    assert calls[0] == calls[1]


def test_pre_commit_python_selection_has_one_owner_native_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_PYTHON", "/opt/operator/python3.12")
    monkeypatch.setenv("PATH", "/operator/bin:/usr/bin")
    calls: list[tuple[tuple[str, ...], dict[str, str], str]] = []

    def fake_run_command(*argv: str, **kwargs: object) -> CommandResult:
        env = dict(cast(dict[str, str], kwargs["env"]))
        project = Path(argv[argv.index("--project") + 1])
        project_text = (project / "pyproject.toml").read_text(encoding="utf-8")
        calls.append((tuple(argv), env, project_text))
        if argv[1] == "lock":
            (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        elif argv[1] == "sync" and "--check" not in argv:
            environment = Path(env["UV_PROJECT_ENVIRONMENT"])
            executable = environment / ("Scripts" if os.name == "nt" else "bin") / (
                "pre-commit.exe" if os.name == "nt" else "pre-commit"
            )
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("", encoding="utf-8")
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr("oh_goon.engine.fleet_tools.run_command", fake_run_command)
    store = FleetToolStore(tmp_path / "plan-tools", path_authority=TEST_PATH_AUTHORITY)
    runtime = store.prepare_pre_commit("pre-commit==4.6.2")

    assert calls
    for _argv, env, project_text in calls:
        assert env["UV_PYTHON"] == "/opt/operator/python3.12"
        assert 'requires-python' not in project_text
        assert 'dependencies = ["pre-commit==4.6.2"]' in project_text
    assert "python_version" not in type(runtime).model_fields
    assert "python" not in runtime.runtime_id.lower()


def test_managed_pre_commit_runtime_contract_has_no_controller_python_field() -> None:
    from oh_goon.core.models.fleet_plan import ManagedToolRuntime, ResolvedPreCommitStep

    assert "python_version" not in ManagedToolRuntime.model_fields
    assert "runtime_python_version" not in ResolvedPreCommitStep.model_fields
