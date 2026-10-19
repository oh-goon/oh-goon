from __future__ import annotations

from typing import Any
from collections.abc import Sequence

from oh_goon.repository_tooling.acceptance.timing import (
    RuntimeAcceptanceTiming,
)

import os
import shutil
import subprocess

import pytest

from pathlib import Path

from oh_goon.repository_tooling.acceptance import local_executor, local_runtime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.strict_executor_authority import STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT


def test_local_executor_preserves_operator_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    environment = {"OH_GOON_EXECUTORS_FILE": "/operator/executors.toml"}
    monkeypatch.setattr(
        local_executor,
        "materialize_local_executor_inventory",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not materialize")),
    )
    configured, issue = local_executor.configure_local_runtime_executor(
        state=tmp_path, environment=environment
    )
    assert configured is True
    assert issue is None
    assert environment["OH_GOON_EXECUTORS_FILE"] == "/operator/executors.toml"


def test_local_executor_materializes_real_inventory_when_operator_omits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inventory = tmp_path / "state/executors.toml"
    environment = {"PATH": "/bin"}

    def materialize(*, state: Path, environment: dict[str, str]) -> Path:
        assert state == tmp_path / "state"
        assert state.is_dir()
        return inventory

    monkeypatch.setattr(
        local_executor,
        "materialize_local_executor_inventory",
        materialize,
    )
    configured, issue = local_executor.configure_local_runtime_executor(
        state=tmp_path / "state", environment=environment
    )
    assert configured is False
    assert issue is None
    assert environment["OH_GOON_EXECUTORS_FILE"] == str(inventory)


def test_runtime_environment_self_binds_serial_executor_but_keeps_parallel_operator_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    inventory = tmp_path / "config/runtime-authority/executors.toml"

    def configure(
        *, state: Path, environment: dict[str, str]
    ) -> tuple[bool, str | None]:
        environment["OH_GOON_EXECUTORS_FILE"] = str(inventory)
        return False, None

    monkeypatch.setattr(local_runtime, "configure_local_runtime_executor", configure)
    environment, operator_configured, issue = (
        local_runtime.prepare_local_runtime_environment(
            temp=tmp_path, state=tmp_path / "state", config=tmp_path / "config"
        )
    )
    assert issue is None
    assert operator_configured is False
    assert environment["OH_GOON_EXECUTORS_FILE"] == str(inventory)


def test_verification_runtime_environment_strips_all_strict_executor_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for index, name in enumerate(STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT):
        monkeypatch.setenv(name, f"inherited-{index}")
    environment = local_runtime.prepare_verification_local_runtime_environment(
        temp=tmp_path,
        state=tmp_path / "state",
        config=tmp_path / "config",
    )
    for name in STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT:
        assert name not in environment


def test_local_runtime_delegation_detects_unusable_current_cgroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling.acceptance import local_delegation

    monkeypatch.delenv(local_delegation.LOCAL_RUNTIME_DELEGATED_ENV, raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        local_delegation,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(FleetPlanInvalid("not delegated")),
    )
    assert local_delegation.needs_systemd_delegation(dict(os.environ)) is True


def test_local_runtime_delegation_stops_reentry_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling.acceptance import local_delegation

    monkeypatch.setattr(os, "name", "posix")
    assert (
        local_delegation.needs_systemd_delegation(
            {local_delegation.LOCAL_RUNTIME_DELEGATED_ENV: "1"}
        )
        is False
    )


def test_local_runtime_systemd_reentry_requests_delegate_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace
    from oh_goon.repository_tooling.acceptance import local_delegation

    observed: dict[str, Any] = {}
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/systemd-run")

    def fake_run(command: Sequence[str], **kwargs: Any) -> SimpleNamespace:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert local_delegation.run_under_systemd_delegation(tmp_path) == 17
    assert "--property=Delegate=yes" in observed["command"]
    assert (
        f"--setenv={local_delegation.LOCAL_RUNTIME_DELEGATED_ENV}=1"
        in observed["command"]
    )
    assert observed["command"][-2:] == (
        "-m",
        "oh_goon.repository_tooling.dev_runtime_acceptance",
    )
    assert observed["kwargs"]["cwd"] == tmp_path


def test_dev_runtime_main_reenters_before_running_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling import dev_runtime_acceptance

    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        dev_runtime_acceptance, "needs_systemd_delegation", lambda _environment: True
    )

    def reenter(
        root: Path, *, environment: dict[str, str], timing: RuntimeAcceptanceTiming
    ) -> int:
        observed["root"] = root
        observed["environment"] = environment
        observed["timing"] = timing
        return 23

    monkeypatch.setattr(dev_runtime_acceptance, "run_under_systemd_delegation", reenter)
    monkeypatch.setattr(
        dev_runtime_acceptance,
        "accept_dev_runtime",
        lambda _root: (_ for _ in ()).throw(
            AssertionError("acceptance must run only inside delegated unit")
        ),
    )
    assert dev_runtime_acceptance.main(("strict",)) == 23
    assert observed["root"] == dev_runtime_acceptance.ROOT
    assert observed["environment"] == dict(os.environ)
    assert observed["timing"] == RuntimeAcceptanceTiming.resolve(
        observed["environment"]
    )


def test_dev_runtime_verification_never_requests_systemd_or_strict_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling import dev_runtime_acceptance

    monkeypatch.setattr(
        dev_runtime_acceptance,
        "needs_systemd_delegation",
        lambda _environment: (_ for _ in ()).throw(
            AssertionError("verification must not inspect delegation")
        ),
    )
    monkeypatch.setattr(
        dev_runtime_acceptance,
        "accept_dev_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict acceptance must not run")
        ),
    )
    monkeypatch.setattr(
        dev_runtime_acceptance, "accept_local_runtime_verification", lambda _root: ()
    )
    assert dev_runtime_acceptance.main(("verification",)) == 0


def test_local_runtime_verification_owns_only_start_access_and_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace
    from oh_goon.repository_tooling import dev_runtime_acceptance

    observed: list[str] = []
    running = SimpleNamespace(
        token="verification-token",
        stop=lambda: observed.append("stop"),
        diagnostic_tail=lambda: (),
    )
    monkeypatch.setattr(dev_runtime_acceptance, "_port_is_free", lambda _port: True)
    monkeypatch.setattr(dev_runtime_acceptance, "_wait_ports_free", lambda: True)
    monkeypatch.setattr(
        dev_runtime_acceptance,
        "prepare_verification_local_runtime_environment",
        lambda **_kwargs: {"APP_ONLY": "1"},
    )

    def launch(root: Path, env: dict[str, str]) -> SimpleNamespace:
        assert root == tmp_path
        assert env == {"APP_ONLY": "1"}
        observed.append("start")
        return running

    def exercise(*, frontend_base: str, token: str, issues: list[str]) -> None:
        assert frontend_base == "http://127.0.0.1:5173"
        assert token == "verification-token"
        assert issues == []
        observed.append("access")

    monkeypatch.setattr(dev_runtime_acceptance, "_launch_dev", launch)
    monkeypatch.setattr(dev_runtime_acceptance, "_exercise_access_boundaries", exercise)
    monkeypatch.setattr(
        dev_runtime_acceptance,
        "prepare_runtime_acceptance_repositories",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "APP runtime verification must not prepare strict Fleet repositories"
            )
        ),
    )
    assert dev_runtime_acceptance.accept_local_runtime_verification(tmp_path) == ()
    assert observed == ["start", "access", "stop"]


def test_operator_parallel_acceptance_rejects_inconsistent_resolved_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        local_runtime,
        "exercise_parallel_shared_plan_cancellation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must fail before deep helper")
        ),
    )
    try:
        local_runtime.exercise_operator_parallel_acceptance(
            enabled=True,
            environment={},
            frontend_base="http://frontend",
            repositories=tmp_path,
            access_token="token",
        )
    except RuntimeError as exc:
        assert "resolved operator executor authority is missing" in str(exc)
    else:
        raise AssertionError(
            "expected fail-closed resolved-authority consistency check"
        )
