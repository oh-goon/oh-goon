from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core import capability_registry
from oh_goon.core.capability_registry import CapabilityRegistry, resolve_bound_capability_intent
from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.vocabulary import (
    CommandSafety,
    EffectBoundaryKind,
    FleetRunTerminalIntent,
    OperationAction,
    ResultFreshness,
    UvRecipeId,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.effect_quarantine import effect_quarantines
from tests.factories.capabilities import admit_test_run, capability_snapshot
from tests.factories.journal import commit_operation, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_state import baseline_for
from tests.factories.schedules import persist_active_schedule, schedule_create


def _registry_with(**changes_by_id: dict[str, object]) -> CapabilityRegistry:
    definitions = []
    for definition in capability_registry.CAPABILITY_DEFINITIONS:
        changes = changes_by_id.get(definition.capability_id)
        definitions.append(replace(definition, **changes) if changes else definition)
    return CapabilityRegistry(tuple(definitions))


def test_direct_attempt_round_trip_preserves_bound_capability_snapshot(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "direct-capability.db")
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.AUDIT)
    snapshot = capability_registry.resolve_capability_intent(
        OperationAction.RECIPE, target
    ).definition.durable_snapshot()

    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            snapshot,
            target=target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    loaded = attempts.get(attempt.operation_id)

    assert loaded.capability == snapshot
    assert loaded.action is OperationAction.RECIPE
    assert loaded.target == target
    assert loaded.safety is snapshot.safety


def test_latest_runs_keep_distinct_capabilities_that_share_one_action(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "latest.db"))
    audit = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.AUDIT)
    sbom = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.SBOM)
    admit_test_run(journal, "fleet-audit", OperationAction.RECIPE, [], audit)
    start_run(journal, "fleet-audit")
    terminalize_run(journal, "fleet-audit", FleetRunTerminalIntent.COMPLETE)
    admit_test_run(journal, "fleet-sbom", OperationAction.RECIPE, [], sbom)
    start_run(journal, "fleet-sbom")
    terminalize_run(journal, "fleet-sbom", FleetRunTerminalIntent.COMPLETE)

    latest = journal.read_view().latest_run_details()
    assert {item.run.capability.capability_id for item in latest} == {"recipe:audit", "recipe:sbom"}
    assert {item.run.fleet_run_id for item in latest} == {"fleet-audit", "fleet-sbom"}


def test_latest_runs_keep_distinct_targets_for_the_same_capability(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "latest-targets.db"))
    first = UvRecipeTarget(
        kind="recipe", recipe=UvRecipeId.RUN_TASK, task="pytest", args=("tests/unit",)
    )
    second = UvRecipeTarget(
        kind="recipe", recipe=UvRecipeId.RUN_TASK, task="pytest", args=("tests/integration",)
    )

    admit_test_run(journal, "fleet-unit", OperationAction.RECIPE, [], first)
    start_run(journal, "fleet-unit")
    terminalize_run(journal, "fleet-unit", FleetRunTerminalIntent.COMPLETE)
    admit_test_run(journal, "fleet-integration", OperationAction.RECIPE, [], second)
    start_run(journal, "fleet-integration")
    terminalize_run(journal, "fleet-integration", FleetRunTerminalIntent.COMPLETE)

    view = journal.read_view()
    latest = view.latest_run_details()
    assert {item.run.fleet_run_id for item in latest} == {"fleet-unit", "fleet-integration"}
    assert view.latest_run_exact("recipe:run-task", first).run.fleet_run_id == "fleet-unit"  # type: ignore[union-attr]
    assert view.latest_run_exact("recipe:run-task", second).run.fleet_run_id == "fleet-integration"  # type: ignore[union-attr]
    assert view.latest_run_family(OperationAction.RECIPE).run.fleet_run_id == "fleet-integration"  # type: ignore[union-attr]


def test_journal_replay_and_freshness_use_durable_capability_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    journal = Journal(LocalDatabase(path))
    commit_operation(
        journal,
        make_operation_report(
            identity, OperationAction.INIT, fleet_run_id=None, operation_id="op-init"
        ),
    )
    commit_operation(
        journal,
        make_operation_report(
            identity,
            OperationAction.UPDATE,
            verdict=Verdict.UNCHANGED,
            fleet_run_id=None,
            operation_id="op-update",
        ),
    )
    before = journal.current_capability(identity.path, "initialize")
    assert before is not None and before.freshness is ResultFreshness.STALE

    monkeypatch.setattr(
        capability_registry,
        "CAPABILITY_REGISTRY",
        _registry_with(update={"invalidates_capabilities": frozenset({"verify"})}),
    )

    restarted = Journal(LocalDatabase(path))
    after = restarted.current_capability(identity.path, "initialize")
    assert after is not None and after.freshness is ResultFreshness.STALE
    persisted_update = restarted.require_operation("op-update")
    assert persisted_update.capability is not None
    assert "initialize" in persisted_update.capability.invalidates_capabilities


def test_fleet_quarantine_uses_admitted_safety_after_registry_policy_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "quarantine.db")
    Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "api"
    repository.mkdir()
    identity = RepositoryIdentity(name="api", path=repository)
    admitted = capability_snapshot(OperationAction.UPDATE)
    operation_id = repository_attempt_operation_id("fleet-uncertain", identity.repository_id)
    with database.transaction(write=True) as connection:
        attempts.begin_fleet_in(
            connection,
            repository,
            admitted,
            fleet_run_id="fleet-uncertain",
            operation_id=operation_id,
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempts.start_effect_in(connection, operation_id)
        attempts.mark_unknown_in(connection, operation_id, note="process ended")

    monkeypatch.setattr(
        capability_registry,
        "CAPABILITY_REGISTRY",
        _registry_with(update={"safety": CommandSafety.READ_ONLY}),
    )

    quarantines = effect_quarantines(attempts.all())
    assert len(quarantines) == 1
    assert quarantines[0].operation_id == operation_id
    assert quarantines[0].safety is CommandSafety.WORKTREE_MUTATION


def test_persisted_schedule_intent_fails_closed_when_capability_policy_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "schedule.db")
    definition = ScheduleDefinitionStore(database).create(
        "schedule-provenance",
        schedule_create(name="queued", repository_ids=("repo-a",)),
    )
    execution = persist_active_schedule(
        database,
        ScheduleExecutionStore(database),
        definition,
    )
    captured = execution.intent.capability

    monkeypatch.setattr(
        capability_registry,
        "CAPABILITY_REGISTRY",
        _registry_with(**{captured.capability_id: {"schedule_supported": False}}),
    )

    with pytest.raises(CapabilityUnavailable, match="policy changed"):
        resolve_bound_capability_intent(captured, execution.intent.target)
