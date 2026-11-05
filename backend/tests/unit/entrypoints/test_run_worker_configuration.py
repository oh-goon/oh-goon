from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest

from oh_goon import _application_worker_runtime as worker
from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon.core.errors import FleetPlanInvalid, OwnershipLost
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime
from tests.factories.capabilities import capability_snapshot


@pytest.fixture
def foundation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RuntimeFoundation]:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    for name, directory in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(name, str(tmp_path / directory))
    value = build_runtime_foundation(
        standard_local=True,
        startup_purpose="workflow",
        git_service=GitService(default_timeout_seconds=13, max_capture_bytes_per_stream=1024),
    )
    try:
        yield value
    finally:
        value.supervisor.shutdown(wait=True)
        value.lifecycle.close()


def test_worker_configuration_keeps_resolved_roots_and_git_authority(
    foundation: RuntimeFoundation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = worker.capture_runtime_worker_configuration(foundation, "selected-run")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "another" / "db.sqlite"))
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "missing.toml"))
    restored = worker.RunWorkerConfiguration.model_validate_json(captured.model_dump_json())
    assert restored == captured
    assert restored.fleet_run_id == "selected-run"
    assert restored.hashmarks_implementation_identity == require_loaded_hashmarks_runtime()
    assert restored.extension_root == foundation.extension_store.root
    assert restored.tool_root == foundation.tool_store.root
    assert restored.content_store_root == foundation.content_store.root
    assert restored.git.restore() == foundation.git_service
    assert restored.standard_local_fleet is True
    assert restored.require_authenticated_fleet is False
    assert restored.fleet_authority is None and restored.executor_configuration is None


def test_injected_git_callbacks_are_refused_before_admission(
    foundation: RuntimeFoundation,
) -> None:
    def callback(*_args: object, **_kwargs: object) -> CommandResult:
        pytest.fail("worker capture must not call or substitute an injected native owner")

    source = replace(foundation, git_service=GitService(command_runner=callback))
    before = source.database.durable_revision()
    with pytest.raises(FleetPlanInvalid, match="without injected callbacks"):
        worker.capture_runtime_worker_configuration(source, "selected-run")
    assert source.database.durable_revision() == before
    assert source.execution.active_lease() is None


def test_unclaimed_ticket_cannot_construct_effectful_worker_runtime(
    foundation: RuntimeFoundation, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "selected-run"
    configuration = worker.capture_runtime_worker_configuration(foundation, run_id)
    journal = foundation.state.journal
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(OperationAction.INIT),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = foundation.execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=foundation.database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection, run_id, intent, fleet_repository_scope(None), []
        ),
    )

    def unexpected(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("effectful construction preceded the canonical worker claim")

    monkeypatch.setattr(worker, "_build_capability_runtime", unexpected)
    before = foundation.database.durable_revision()
    with pytest.raises(OwnershipLost):
        worker.build_claimed_run_execution(
            foundation.database,
            ticket,
            configuration,
            terminal=None,
            live_output=foundation.live_output,
            diagnostics=foundation.diagnostics,
        )
    assert foundation.database.durable_revision() == before
