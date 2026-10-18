from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon import workflow as workflow_run
from oh_goon.core.strict_executor_authority import STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
MODULE_PATH = Path(workflow_run.__file__).resolve()


def _seed_product_version(root: Path, monkeypatch) -> None:
    (root / "VERSION").write_text(EXPECTED_VERSION + "\n", encoding="utf-8")


def test_certification_product_version_comes_from_checkout_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)

    assert workflow_run._certification_product_version() == EXPECTED_VERSION


def test_certification_product_version_is_not_a_package_metadata_mirror(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    (tmp_path / "VERSION").write_text("9999.0.0\n", encoding="utf-8")

    assert workflow_run._certification_product_version() == "9999.0.0"




def test_main_explicit_empty_args_cannot_inherit_frontdoor_process_args(monkeypatch) -> None:
    monkeypatch.setattr(
        workflow_run.sys,
        "argv",
        [
            "oh-goon-workflow",
            "--oh-goon-delegated-reentry",
            "/run/user/1000/environment.json",
            "/run/user/1000/ready",
        ],
    )
    monkeypatch.setattr(workflow_run, "run_app", lambda: 17)

    assert workflow_run.main([]) == 17


def test_make_workflow_entrypoints_use_uv_owned_python() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    lines = makefile.splitlines()
    expected = {
        "certify": "$(CERTIFICATION_FRONTDOOR)",
        "prepare-release": "$(CERTIFICATION_RUNNER)",
        "certification-explain": "$(CERTIFICATION_RUNNER)",
    }
    for target, owner in expected.items():
        start = next(i for i, line in enumerate(lines) if line.startswith(target + ":"))
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace() and ":" in lines[i]),
            len(lines),
        )
        snippet = "\n".join(lines[start:end])
        assert owner in snippet
        assert "uv run --python" not in snippet


def test_environment_classifier_covers_common_first_start_failures() -> None:
    hints = " ".join(workflow_run.ENVIRONMENT_HINTS)
    for fragment in (
        "certificate",
        "proxy",
        "host system is missing dependencies",
        "read-only file system",
        "unable to open database file",
    ):
        assert fragment in hints


def test_runtime_storage_loss_is_environment_not_application() -> None:
    for output in (
        "OSError: [Errno 30] Read-only file system",
        "sqlite3.OperationalError: unable to open database file",
        "sqlite3.OperationalError: disk I/O error",
    ):
        assert workflow_run._classify(output) == "environment"


def test_missing_g00n_setup_tool_is_environment_not_application() -> None:
    for output in (
        "node check failed: missing — [Errno 2] No such file or directory",
        "npm check failed: missing — [Errno 2] No such file or directory",
        "uv check failed: missing — [Errno 2] No such file or directory",
    ):
        assert workflow_run._classify(output) == "environment"


def test_build_backend_source_failure_is_application_not_environment() -> None:
    output = "The build backend returned an error: Forced include not found: generated/release/static"
    assert workflow_run._classify(output) == "application"


def test_terminal_forced_include_failure_outranks_earlier_environment_like_tail_text() -> None:
    output = (
        "earlier diagnostic: browser dependencies are missing\n"
        "The build backend returned an error: Forced include not found: /workspace/generated/release/static"
    )
    assert workflow_run._classify(output) == "application"


def test_concrete_network_failure_is_environment_blocker() -> None:
    assert (
        workflow_run._classify("failed to download: temporary failure in name resolution")
        == "environment"
    )

def test_offline_app_treats_dependency_acquisition_as_application_contract_failure() -> None:
    assert workflow_run._app_attempted_dependency_acquisition(
        "failed to fetch https://pypi.org/simple/hatchling/"
    )
    assert not workflow_run._app_attempted_dependency_acquisition(
        "browser runtime preflight failed: missing libX11"
    )


def test_run_has_backend_context_then_one_bootstrap_sequence_then_one_plan_run(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setattr(
        workflow_run,
        "_certification_environment",
        lambda: (
            "repo-test",
            {
                "OH_GOON_DB": "/state/db",
                "OH_GOON_LEAGUE_FILE": "/state/league.yml",
                "OH_GOON_EXECUTORS_FILE": "/state/executors.toml",
            },
            Path("/state/locks"),
        ),
    )

    calls: list[list[str]] = []

    def fake_capture(args: list[str], *, env=None):
        calls.append(args)
        return Mock(returncode=0, stdout="")

    streamed: list[list[str]] = []

    def fake_stream(args: list[str], *, env):
        streamed.append(args)
        return 0, ""

    monkeypatch.setattr(workflow_run, "_backend_cli", fake_capture)
    monkeypatch.setattr(workflow_run, "_stream", fake_stream)

    assert workflow_run.run_app() == 0
    assert calls[0] == ["league", "add-folder", str(tmp_path)]
    assert calls[1] == [
        "lock",
        workflow_run.APP_PLAN,
        "--output",
        "/state/locks/app-certification.goon.lock",
    ]
    assert len(streamed) == 1
    cli = str(Path(workflow_run.os.path.abspath(workflow_run.sys.executable)).parent / "oh-goon")
    assert streamed[0][:4] == [cli, "plan", "run", workflow_run.APP_PLAN]
    assert "--repository-id" in streamed[0]
    assert streamed[0][streamed[0].index("--lock-file") + 1] == "/state/locks/app-certification.goon.lock"



def test_self_verify_selects_fast_plan_through_same_plan_run(tmp_path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    monkeypatch.setattr(
        workflow_run,
        "_certification_environment",
        lambda: (
            "repo-test",
            {
                "OH_GOON_DB": "/state/db",
                "OH_GOON_LEAGUE_FILE": "/state/league.yml",
                "OH_GOON_EXECUTORS_FILE": "/state/executors.toml",
            },
            Path("/state/locks"),
        ),
    )
    calls: list[list[str]] = []
    streamed: list[list[str]] = []

    def fake_capture(args: list[str], *, env=None):
        calls.append(args)
        return Mock(returncode=0, stdout="")

    def fake_stream(args: list[str], *, env):
        streamed.append(args)
        return 0, ""

    monkeypatch.setattr(workflow_run, "_backend_cli", fake_capture)
    monkeypatch.setattr(workflow_run, "_stream", fake_stream)

    assert workflow_run.run_self_verify() == 0
    assert calls[0] == ["league", "add-folder", str(tmp_path)]
    assert calls[1][0:2] == ["lock", workflow_run.SELF_VERIFY_PLAN]
    assert len(streamed) == 1
    assert streamed[0][1:4] == ["plan", "run", workflow_run.SELF_VERIFY_PLAN]
    assert not any("dependency_supply_reference=" in item for item in streamed[0])


def test_workflow_main_verify_selects_self_verify(monkeypatch) -> None:
    monkeypatch.setattr(workflow_run, "run_self_verify", lambda: 23)
    assert workflow_run.main(["verify"]) == 23



def test_classifier_does_not_treat_generic_sudo_or_missing_dependencies_as_environment() -> None:
    output = "application test failed: missing dependencies in model graph; sudo owner is invalid"
    assert workflow_run._classify(output) == "application"


def test_backend_cli_sibling_uses_invoked_venv_path_without_resolving_python_symlink(tmp_path, monkeypatch) -> None:
    venv_bin = tmp_path / "backend/.venv/bin"
    venv_bin.mkdir(parents=True)
    base_python = tmp_path / "uv-python/bin/python"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("", encoding="utf-8")
    invoked_python = venv_bin / "python"
    invoked_python.symlink_to(base_python)

    monkeypatch.setattr(workflow_run.sys, "executable", str(invoked_python))

    assert workflow_run._backend_cli_executable() == str(venv_bin / "oh-goon")
    assert Path(workflow_run.sys.executable).resolve().parent != venv_bin




def test_app_bootstrap_returns_control_authority_without_strict_executor(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/operator/strict-executors.toml")
    monkeypatch.setenv("OH_GOON_CERTIFICATION_CGROUP_ROOT", "/sys/fs/cgroup/operator")
    monkeypatch.setenv("OH_GOON_CERTIFICATION_SYSTEMD_DELEGATED", "1")
    monkeypatch.setenv("OH_GOON_CERTIFICATION_SYSTEMD_SUBGROUP", "strict-work")
    payload = {
        "repository_id": "repo-123",
        "state_root": "/state",
        "database": "/state/oh-goon.db",
        "league_file": "/state/league.yml",
        "lock_root": "/state/locks",
    }
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_capture(args: list[str], *, env=None):
        calls.append((args, dict(env or {})))
        return Mock(
            returncode=0,
            stdout=workflow_run.certification_bootstrap_protocol.render_success_record(payload) + "\n",
        )

    monkeypatch.setattr(workflow_run, "_backend_cli", fake_capture)
    repo_id, env, lock_root = workflow_run._certification_environment()

    assert repo_id == "repo-123"
    assert env["OH_GOON_DB"] == payload["database"]
    assert env["OH_GOON_LEAGUE_FILE"] == payload["league_file"]
    for name in STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT:
        assert name not in env
    assert lock_root == Path(payload["lock_root"])
    assert calls[0][0] == [
        "dev",
        "bootstrap-certification",
        "--state-root",
        str(tmp_path / "state-home"),
    ]
    for name in STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT:
        assert name not in calls[0][1]


def test_release_bootstrap_explicitly_requests_and_exports_strict_executor(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    payload = {
        "repository_id": "repo-123",
        "state_root": "/state",
        "database": "/state/oh-goon.db",
        "league_file": "/state/league.yml",
        "executors_file": "/state/executors.toml",
        "lock_root": "/state/locks",
    }
    calls: list[list[str]] = []

    def fake_capture(args: list[str], *, env=None):
        calls.append(args)
        return Mock(
            returncode=0,
            stdout=workflow_run.certification_bootstrap_protocol.render_success_record(payload) + "\n",
        )

    monkeypatch.setattr(workflow_run, "_backend_cli", fake_capture)
    repo_id, env, lock_root = workflow_run._certification_environment(
        require_strict_executor=True
    )

    assert repo_id == "repo-123"
    assert env["OH_GOON_EXECUTORS_FILE"] == payload["executors_file"]
    assert lock_root == Path(payload["lock_root"])
    assert calls == [[
        "dev",
        "bootstrap-certification",
        "--state-root",
        str(tmp_path / "state-home"),
        "--require-strict-executor",
    ]]


def test_app_bootstrap_rejects_accidental_strict_executor_authority(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    payload = {
        "repository_id": "repo-123",
        "state_root": "/state",
        "database": "/state/oh-goon.db",
        "league_file": "/state/league.yml",
        "executors_file": "/state/executors.toml",
        "lock_root": "/state/locks",
    }
    monkeypatch.setattr(
        workflow_run,
        "_backend_cli",
        lambda _args, env=None: Mock(
            returncode=0,
            stdout=workflow_run.certification_bootstrap_protocol.render_success_record(payload) + "\n",
        ),
    )

    with pytest.raises(workflow_run.CertificationContextFailure, match="mismatched strict executor authority"):
        workflow_run._certification_environment()

def test_backend_bootstrap_environment_failure_is_structured(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    error = {"classification": "environment", "message": "bwrap is missing"}
    monkeypatch.setattr(
        workflow_run,
        "_backend_cli",
        lambda _args, env=None: Mock(
            returncode=2,
            stdout=workflow_run.certification_bootstrap_protocol.render_error_record(error) + "\n",
        ),
    )

    assert workflow_run.run_app() == 2
    output = capsys.readouterr().out
    assert workflow_run.CERTIFICATION_CONTEXT_FAILURE_PREFIX in output
    assert "Command: oh-goon dev bootstrap-certification" in output
    assert "bwrap is missing" in output
    assert "BLOCKED BY ENVIRONMENT" in output
    assert "No certification Goon ran" in output


def test_backend_bootstrap_unstructured_failure_is_application(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setattr(
        workflow_run,
        "_backend_cli",
        lambda _args, env=None: Mock(returncode=1, stdout="unexpected source defect\n"),
    )

    assert workflow_run.run_app() == 2
    output = capsys.readouterr().out
    assert "APPLICATION FAILED" in output
    assert "backend certification bootstrap returned an invalid error response" in output
    assert "unexpected source defect" not in output
    assert "BLOCKED BY ENVIRONMENT" not in output


def test_workflow_does_not_reimplement_executor_or_backend_domain_rules() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "observe_local_executor_capacity",
        "_configure_certification_executor",
        "_certification_cgroup_root",
        "bwrap_executable",
        "cgroup.controllers",
        "memory_mib",
        "max_processes =",
        "oh_goon.runtime.",
        "oh_goon.services.",
    ):
        assert forbidden not in source


def test_plan_bootstrap_failure_is_rendered_with_command_and_real_cause(
    tmp_path, monkeypatch, capsys
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_run, "_certification_environment", lambda: ("repo-test", {}, Path("/state/locks")))
    monkeypatch.setattr(
        workflow_run,
        "_backend_cli",
        lambda command, *, env: Mock(
            returncode=1,
            stdout="failed to download: temporary failure in name resolution\n",
        ),
    )

    assert workflow_run.run_app() == 2
    output = capsys.readouterr().out
    assert workflow_run.BOOTSTRAP_FAILURE_PREFIX in output
    assert "Command: oh-goon league add-folder" in output
    assert "temporary failure in name resolution" in output
    assert "BLOCKED BY ENVIRONMENT" in output
    assert "No certification Goon ran" in output


def test_plan_bootstrap_source_failure_is_not_misclassified_as_environment(
    tmp_path, monkeypatch, capsys
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_run, "_certification_environment", lambda: ("repo-test", {}, Path("/state/locks")))
    monkeypatch.setattr(
        workflow_run,
        "_backend_cli",
        lambda command, *, env: Mock(returncode=1, stdout="invalid canonical Plan source\n"),
    )

    assert workflow_run.run_app() == 2
    output = capsys.readouterr().out
    assert workflow_run.BOOTSTRAP_FAILURE_PREFIX in output
    assert "APPLICATION FAILED" in output
    assert "BLOCKED BY ENVIRONMENT" not in output


def test_native_dependency_network_failure_is_environment_blocker(
    tmp_path, monkeypatch, capsys
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_run, "_certification_environment", lambda: ("repo-test", {}, Path("/state/locks")))
    monkeypatch.setattr(
        workflow_run,
        "_run_plan",
        lambda **_kwargs: (
            1,
            "CERTIFICATION PLAN FAILED\nfailed to download https://pypi.org/simple/hatchling/\n",
        ),
    )

    assert workflow_run.run_app() == 1
    output = capsys.readouterr().out
    assert "BLOCKED BY ENVIRONMENT" in output
    assert "APPLICATION FAILED" not in output
    assert "Fix the reported environment cause, then rerun: make certify" in output



def test_success_labels_app_certification_not_generic_ready(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    _seed_product_version(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_run, "_certification_environment", lambda: ("repo-test", {}, Path("/state/locks")))
    monkeypatch.setattr(workflow_run, "_run_plan", lambda **_kwargs: (0, ""))
    assert workflow_run.run_app() == 0
    output = capsys.readouterr().out
    assert "OH-GOON CERTIFICATION IDENTITY" in output
    assert f"Product version: {EXPECTED_VERSION}" in output
    assert "APP CERTIFICATION PASSED" in output


def test_missing_own_backend_module_is_application_failure() -> None:
    output = (
        "ModuleNotFoundError: No module named "
        "'oh_goon.integrations.fleet_plan_kubernetes_step_execution'"
    )
    assert workflow_run._classify(output) == "application"


def test_project_command_surfaces_use_named_uv_entrypoints() -> None:
    frontend = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    migration = (ROOT / "docs/development/GITLAB_MIGRATION.md").read_text(encoding="utf-8")
    assert "--project ../backend python " not in frontend
    assert "--project backend python " not in migration
    assert "python3 " not in migration


def test_workflow_checkout_root_is_command_working_directory() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "ROOT = Path.cwd().resolve()" in source
    assert "Path(__file__).resolve().parents" not in source


def test_enomem_bootstrap_failure_is_environment_classified() -> None:
    rendered = "OSError: [Errno 12] Cannot allocate memory: '<repo>/backend/src/oh_goon'"
    assert workflow_run._classify(rendered) == "environment"


def test_raw_eio_bootstrap_failure_is_environment_classified() -> None:
    assert workflow_run._classify("OSError: [Errno 5] Input/output error") == "environment"


def test_backend_cli_forwards_only_activity_records_live(monkeypatch) -> None:
    class FakeProcess:
        stdout = iter(
            (
                "OH-GOON ACTIVITY: verifying immutable dependency bytes\n",
                'OH_GOON_CERTIFICATION_DEPENDENCIES={"sha256":"abc"}\n',
            )
        )

        def wait(self) -> int:
            return 0

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(workflow_run, "_backend_cli_executable", lambda: "/env/bin/oh-goon")
    monkeypatch.setattr(workflow_run.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    observed: list[str] = []
    monkeypatch.setattr(workflow_run, "_transient_activity", observed.append)

    result = workflow_run._backend_cli(["dev", "probe"], env={})

    assert result.returncode == 0
    assert observed == [" verifying immutable dependency bytes\n"]
    assert "OH_GOON_CERTIFICATION_DEPENDENCIES=" in result.stdout
