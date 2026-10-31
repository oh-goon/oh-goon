from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError, field_validator

from oh_goon.cli import main
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import NativeAgentName
from oh_goon.runtime.agent_providers import load_agent_providers


@pytest.fixture
def setup_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    import oh_goon.cli as cli
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("native setup must not construct a Run or request inference")

    monkeypatch.setattr(cli, "_build_application_runtime", forbidden)
    monkeypatch.setattr(AgentProviderClient, "complete", forbidden)
    workspace = tmp_path / "workspaces/project"
    workspace.mkdir(parents=True)
    home = tmp_path / "native-state"
    home.mkdir()
    (home / "auth.json").write_text("NATIVE_LOGIN_CANARY")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "never-created.sqlite"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "never-created-league.yml"))
    return {"root": tmp_path, "workspace": workspace, "home": home}


def _executable(root: Path, harness: str, *, version: str | None = None) -> Path:
    path = root / "tools" / harness
    path.parent.mkdir(exist_ok=True)
    text = version or {"codex": "codex-cli 0.164.0", "pi": "1.3.0", "opencode": "2.1.2"}[harness]
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:] == ['--version']\n"
        f"Path({str(root / 'version-argv.json')!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({text!r})\n"
    )
    path.chmod(0o755)
    return path


def _catalog(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "default_model": "gateway",
        "default_native_profile": "preserved",
        "providers": {
            "company": {
                "api": "openai-responses",
                "base_url": "https://gateway.example/v1",
                "api_key_env": "ABSENT_SETUP_KEY",
            }
        },
        "models": {"gateway": {"provider": "company", "model": "native-model-default"}},
        "native_profiles": {"preserved": {"backend": "local", "timeout_seconds": 42.0}},
    }
    path.write_text(json.dumps(payload))
    return payload


def _setup_args(paths: dict[str, Path], executable: Path, catalog: Path) -> list[str]:
    return [
        "agent",
        "setup",
        executable.name,
        "--config",
        str(catalog),
        "--executable",
        str(executable),
        "--workspace-root",
        str(paths["workspace"]),
        "--mode",
        "managed",
        "--no-input",
        "--json",
    ]


@pytest.mark.process
@pytest.mark.parametrize("harness", ["codex", "pi", "opencode"])
def test_native_setup_merges_catalog_preserves_native_state_and_observes_only_version(
    setup_paths: dict[str, Path],
    harness: NativeAgentName,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    before = _catalog(catalog)
    executable = _executable(paths["root"], harness)
    assert main(_setup_args(paths, executable, catalog)) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert report["status"] == "configured_not_qualified"
    assert report["qualification"] == "NOT_QUALIFIED" and report["execution_admitted"] is False
    assert (
        report["native_version"]
        == {"codex": "0.164.0", "pi": "1.3.0", "opencode": "2.1.2"}[harness]
    )
    assert json.loads((paths["root"] / "version-argv.json").read_text()) == ["--version"]
    authored = json.loads(catalog.read_text())
    for key, value in before.items():
        if key == "native_profiles":
            assert authored[key]["preserved"] == value["preserved"]
        else:
            assert authored[key] == value
    config = load_agent_providers(catalog)
    assert config.native_profiles[f"{harness}-managed"].inference_profile == "gateway"
    assert config.native_agents[harness].executable == str(executable)
    assert (paths["home"] / "auth.json").read_text() == "NATIVE_LOGIN_CANARY"
    assert "NATIVE_LOGIN_CANARY" not in captured.out + captured.err + catalog.read_text()
    assert not (paths["root"] / "never-created.sqlite").exists()
    assert not (paths["root"] / "never-created-league.yml").exists()


@pytest.mark.process
@pytest.mark.parametrize("mode", ["codex-login", "codex-service"])
def test_native_codex_setup_creates_explicit_protected_profile_and_default(
    setup_paths: dict[str, Path], mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = setup_paths
    executable = _executable(paths["root"], "codex")
    catalog = paths["root"] / "new/providers.json"
    arguments = _setup_args(paths, executable, catalog)
    arguments[arguments.index("managed")] = mode
    arguments.extend(
        [
            "--home",
            str(paths["home"]),
            "--model",
            "chosen-native-model",
            "--reasoning-effort",
            "low",
            "--make-default",
        ]
    )
    assert main(arguments) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["native_authentication"] == "unverified"
    assert report["default_native_profile"] == f"codex-{mode}"
    config = load_agent_providers(catalog)
    profile = config.native_profiles[f"codex-{mode}"]
    connection = profile.codex_login if mode == "codex-login" else profile.codex_service
    assert connection is not None
    assert connection.home == str(paths["home"])
    assert connection.model == "chosen-native-model" and connection.reasoning_effort == "low"
    assert not config.providers and not config.models
    assert (paths["home"] / "auth.json").read_text() == "NATIVE_LOGIN_CANARY"
    assert (catalog.parent / "providers.schema.json").is_file()


@pytest.mark.parametrize(
    "defect",
    ["missing-mode", "missing-workspace", "missing-home", "foreign-mode", "missing-inference"],
)
def test_native_setup_missing_or_incompatible_policy_refuses_before_native_probe(
    setup_paths: dict[str, Path],
    defect: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid setup reached a native version probe")

    monkeypatch.setattr(command, "_native_setup_version", forbidden)
    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    arguments = ["agent", "setup", "codex", "--config", str(catalog), "--no-input", "--json"]
    if defect != "missing-mode":
        arguments.extend(
            [
                "--mode",
                "managed"
                if defect in {"missing-workspace", "missing-inference"}
                else "codex-login",
            ]
        )
    if defect != "missing-workspace":
        arguments.extend(["--workspace-root", str(paths["workspace"])])
    if defect == "foreign-mode":
        arguments[2] = "pi"
    assert main(arguments) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "rejected"
    assert not catalog.exists()


@pytest.mark.process
def test_native_setup_refuses_existing_profile_and_reuses_existing_agent_definition(
    setup_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    _catalog(catalog)
    executable = _executable(paths["root"], "pi")
    arguments = _setup_args(paths, executable, catalog)
    assert main(arguments) == 0
    capsys.readouterr()
    original = catalog.read_bytes()
    assert main(arguments) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert catalog.read_bytes() == original
    start = arguments.index("--executable")
    del arguments[start : start + 2]
    arguments.extend(["--profile", "second-pi"])
    assert main(arguments) == 0
    capsys.readouterr()
    after = json.loads(catalog.read_text())
    assert after["native_agents"] == json.loads(original)["native_agents"]


@pytest.mark.process
def test_native_setup_cas_preserves_catalog_changed_during_native_observation(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    payload = _catalog(catalog)
    executable = _executable(paths["root"], "pi")
    probe = command._native_setup_version
    concurrent: list[bytes] = []

    def replace_catalog(path: Path, harness: Any) -> str:
        version = probe(path, harness)
        payload["models"]["concurrent"] = {"provider": "company", "model": "other-model"}
        catalog.write_text(json.dumps(payload))
        concurrent.append(catalog.read_bytes())
        return version

    monkeypatch.setattr(command, "_native_setup_version", replace_catalog)
    assert main(_setup_args(paths, executable, catalog)) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "AGENT_PROVIDER_ERROR"
    assert catalog.read_bytes() == concurrent[0]
    assert load_agent_providers(catalog).native_profiles.keys() == {"preserved"}


@pytest.mark.process
def test_native_setup_does_not_publish_or_echo_unrecognized_private_version_output(
    setup_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    _catalog(catalog)
    before = catalog.read_bytes()
    executable = _executable(paths["root"], "pi", version="PRIVATE_NATIVE_OUTPUT_CANARY")
    assert main(_setup_args(paths, executable, catalog)) == 2
    output = capsys.readouterr()
    assert "PRIVATE_NATIVE_OUTPUT_CANARY" not in output.out + output.err
    assert json.loads(output.out)["status"] == "rejected"
    assert catalog.read_bytes() == before


def test_native_setup_refuses_toml_authoring_before_probe_and_preserves_bytes(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    catalog = paths["root"] / "providers.toml"
    payload = b"version = 1\n# Keep caller TOML byte-for-byte.\n"
    catalog.write_bytes(payload)
    executable = _executable(paths["root"], "pi")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("unsupported catalog authoring reached a native probe")

    monkeypatch.setattr(command, "_native_setup_version", forbidden)
    assert main(_setup_args(paths, executable, catalog)) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["code"] == "UNSUPPORTED_CATALOG_FORMAT"
    assert "TOML" in report["safe_next_action"]
    assert catalog.read_bytes() == payload
    assert not catalog.with_suffix(".schema.json").exists()


def test_native_setup_validates_all_workspace_roots_before_probe(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    _catalog(catalog)
    before = catalog.read_bytes()
    executable = _executable(paths["root"], "pi")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("unavailable workspace root reached native work")

    monkeypatch.setattr(command, "_native_setup_version", forbidden)
    assert (
        main(
            [
                *_setup_args(paths, executable, catalog),
                "--workspace-root",
                str(paths["root"] / "missing-root"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert catalog.read_bytes() == before


@pytest.mark.parametrize("option", ["--profile", "--inference-profile"])
def test_native_setup_refuses_explicit_blank_selection_without_using_catalog_default(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: str,
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    original = _catalog(catalog)
    baseline = load_agent_providers(catalog)
    assert baseline.default_model == "gateway" and baseline.default_native_profile == "preserved"
    before = catalog.read_bytes()
    executable = _executable(paths["root"], "pi")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("explicit invalid selection reached a native probe")

    monkeypatch.setattr(command, "_native_setup_version", forbidden)
    assert main([*_setup_args(paths, executable, catalog), option, ""]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "rejected"
    assert catalog.read_bytes() == before
    preserved = json.loads(catalog.read_bytes())
    assert preserved["default_model"] == original["default_model"]
    assert preserved["default_native_profile"] == original["default_native_profile"]
    assert not (paths["root"] / "version-argv.json").exists()


@pytest.mark.parametrize("mode", ["codex-login", "codex-service"])
@pytest.mark.parametrize(
    "overlap", ["catalog", "catalog-symlink", "schema-symlink", "requested-schema-symlink"]
)
def test_protected_native_setup_rejects_catalog_and_schema_aliases_before_private_reads(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    overlap: str,
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    home = paths["home"]
    private = home / "private-catalog.json"
    _catalog(private)
    assert load_agent_providers(private).default_model == "gateway"
    auth = home / "auth.json"
    before_private = {private: private.read_bytes(), auth: auth.read_bytes()}
    catalog = paths["root"] / "providers.json"
    if overlap == "catalog":
        catalog = private
    elif overlap == "catalog-symlink":
        catalog.symlink_to(private)
    else:
        _catalog(catalog)
        if overlap == "requested-schema-symlink":
            requested = paths["root"] / "alias.json"
            requested.symlink_to(catalog)
            requested.with_suffix(".schema.json").symlink_to(auth)
            catalog = requested
        else:
            catalog.with_suffix(".schema.json").symlink_to(auth)
    original_open = os.open

    def no_private_open(
        target: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(target).resolve().is_relative_to(home):
            pytest.fail("setup opened a native private file")
        return original_open(target, flags, mode, dir_fd=dir_fd)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("native-home overlap reached a version probe")

    executable = _executable(paths["root"], "codex")
    arguments = _setup_args(paths, executable, catalog)
    arguments[arguments.index("managed")] = mode
    arguments.extend(
        ["--home", str(home), "--model", "selected-model", "--reasoning-effort", "low"]
    )
    with monkeypatch.context() as boundary:
        boundary.setattr(os, "open", no_private_open)
        boundary.setattr(command, "_native_setup_version", forbidden)
        assert main(arguments) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "rejected" and report["code"] == "AGENT_PROVIDER_ERROR"
    for path, data in before_private.items():
        assert path.read_bytes() == data
    assert not (paths["root"] / "version-argv.json").exists()


@pytest.mark.parametrize("entry", ["hardlink", "fifo"])
def test_native_setup_rejects_non_authoring_config_before_opening_content(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    private = paths["home"] / "private-catalog.json"
    _catalog(private)
    assert load_agent_providers(private).default_native_profile == "preserved"
    before = private.read_bytes()
    catalog = paths["root"] / "providers.json"
    if entry == "hardlink":
        os.link(private, catalog)
    else:
        os.mkfifo(catalog)
    original_open = os.open

    def no_catalog_open(
        target: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(target) == catalog:
            pytest.fail("unsafe catalog was opened before authoring refusal")
        return original_open(target, flags, mode, dir_fd=dir_fd)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("unsafe catalog reached a version probe")

    executable = _executable(paths["root"], "pi")
    with monkeypatch.context() as boundary:
        boundary.setattr(os, "open", no_catalog_open)
        boundary.setattr(command, "_native_setup_version", forbidden)
        assert main(_setup_args(paths, executable, catalog)) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert private.read_bytes() == before
    assert not (paths["root"] / "version-argv.json").exists()


def test_native_setup_requires_loaded_hashmarks_before_probe_or_publication(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command
    from oh_goon.integrations.hashmarks import runtime_contract

    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    _catalog(catalog)
    executable = _executable(paths["root"], "pi")

    def unavailable() -> Any:
        raise RuntimeError("PRIVATE_LOADED_IDENTITY_CANARY")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("native setup effect preceded loaded Hashmarks identity")

    monkeypatch.setattr(runtime_contract, "require_loaded_hashmarks_runtime", unavailable)
    monkeypatch.setattr(command, "_native_setup_version", forbidden)
    before = catalog.read_bytes()
    assert main(_setup_args(paths, executable, catalog)) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "CLI_COMMAND_FAILED"
    assert "PRIVATE_LOADED_IDENTITY_CANARY" not in output.out + output.err
    assert catalog.read_bytes() == before


@pytest.mark.process
def test_native_setup_refuses_executable_changed_during_version_probe(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    _catalog(catalog)
    executable = _executable(paths["root"], "pi")
    before = catalog.read_bytes()
    probe = command._native_setup_version

    def replace_executable(path: Path, harness: NativeAgentName) -> str:
        version = probe(path, harness)
        path.write_text(path.read_text() + "\n# changed after observation\n")
        return version

    monkeypatch.setattr(command, "_native_setup_version", replace_executable)
    assert main(_setup_args(paths, executable, catalog)) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "AGENT_PROVIDER_ERROR"
    assert catalog.read_bytes() == before


@pytest.mark.parametrize(
    "failure", [OSError("PRIVATE_EXCEPTION_CANARY"), ValueError("PRIVATE_EXCEPTION_CANARY")]
)
def test_provider_configuration_refusal_does_not_echo_raw_exception_payload(
    setup_paths: dict[str, Path],
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import oh_goon.cli_agent_providers as command

    def reject(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(command, "load_agent_providers", reject)
    assert (
        command.run_provider_command(
            Namespace(
                config=setup_paths["root"] / "providers.json",
                provider_command="validate",
                as_json=True,
            )
        )
        == 2
    )
    output = capsys.readouterr()
    assert "PRIVATE_EXCEPTION_CANARY" not in output.out + output.err
    assert output.err == ""
    report = json.loads(output.out)
    assert report["schema"] == "oh-goon.provider-refusal/v1"
    assert report["code"] == (
        "PROVIDER_IO_FAILED" if isinstance(failure, OSError) else "PROVIDER_REQUEST_INVALID"
    )


@pytest.mark.parametrize("surface", ["provider", "native-setup"])
@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("failure_kind", ["domain", "validation"])
def test_provider_and_native_refusals_exclude_domain_notes_and_validation_context(
    setup_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    surface: str,
    as_json: bool,
    failure_kind: str,
) -> None:
    import oh_goon.cli_agent_providers as command

    paths = setup_paths
    catalog = paths["root"] / "providers.json"
    _catalog(catalog)
    original = catalog.read_bytes()
    failure: Exception
    if failure_kind == "domain":
        failure = FleetPlanInvalid("PRIVATE_DOMAIN_EXCEPTION_CANARY")
        expected_code = "FLEET_PLAN_INVALID"
    else:

        class InvalidCatalogField(BaseModel):
            value: str

            @field_validator("value")
            @classmethod
            def reject(cls, value: str) -> str:
                raise ValueError("PRIVATE_VALIDATION_CONTEXT_CANARY")

        with pytest.raises(ValidationError) as invalid:
            InvalidCatalogField(value="PRIVATE_VALIDATION_INPUT_CANARY")
        failure = invalid.value
        expected_code = "PROVIDER_CONFIGURATION_INVALID"
    failure.add_note("PRIVATE_EXCEPTION_NOTE_CANARY")

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise failure

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a refused catalog reached native work")

    monkeypatch.setattr(command, "_native_setup_version", forbidden)
    if surface == "provider":
        monkeypatch.setattr(command, "load_agent_providers", refuse)
        arguments = ["agent", "providers", "validate", "--config", str(catalog)]
    else:
        monkeypatch.setattr(command, "read_agent_provider_catalog", refuse)
        arguments = _setup_args(paths, _executable(paths["root"], "pi"), catalog)
        arguments.remove("--json")
    if as_json:
        arguments.append("--json")
    assert main(arguments) == 2
    output = capsys.readouterr()
    combined = output.out + output.err
    for canary in (
        "PRIVATE_DOMAIN_EXCEPTION_CANARY",
        "PRIVATE_VALIDATION_CONTEXT_CANARY",
        "PRIVATE_VALIDATION_INPUT_CANARY",
        "PRIVATE_EXCEPTION_NOTE_CANARY",
    ):
        assert canary not in combined
    assert catalog.read_bytes() == original
    if as_json:
        assert output.err == ""
        report = json.loads(output.out)
        assert report["code"] == expected_code
        assert report["status"] == "rejected" and report["safe_next_action"]
        if surface == "provider":
            assert report["schema"] == "oh-goon.provider-refusal/v1"
        else:
            assert report["schema_version"] == "oh-goon.native-agent-setup/v1"
            assert report["execution_admitted"] is False
    else:
        assert output.out == ""
        assert expected_code in output.err
