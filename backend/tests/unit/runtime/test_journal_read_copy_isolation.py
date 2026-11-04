from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.effect_identity import RepositoryEffectIdentity
from oh_goon.core.models.effect_origin import DirectEffectOrigin
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.reconciliation import (
    PartiallyAppliedRepositoryReconciliation,
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import (
    EffectAttemptOwner,
    FleetRunEventType,
    OperationAction,
    ReconciliationOutcome,
)
from oh_goon.runtime.journal_facts import (
    EffectAcknowledgementCommitted,
    EffectReconciliationCommitted,
    RunEventCommitted,
)
from oh_goon.runtime.journal_view import JournalReadView
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.repository_state import baseline_for
from tests.factories.run_admission import repository_admissions_for


@pytest.fixture
def journal_view(tmp_path: Path) -> JournalReadView:
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repository"})
    connection = AgentConnection(
        profile="review",
        provider_name="company",
        provider=AgentProvider(api="openai-chat-completions", base_url="http://127.0.0.1:12345/v1"),
        selection=AgentModel(
            provider="company",
            model="review-model",
            parameters={"custom": {"items": ["recorded"]}},
        ),
    )
    plan = ResolvedFleetPlan.create(
        name="read-copy-isolation",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="inspect", argv=("true",), timeout_seconds=10),),
        native_agent=ResolvedNativeAgent(
            agent="pi",
            profile_name="isolated",
            definition=NativeAgentDefinition(executable="/native/pi"),
            profile=NativeAgentProfile(backend="bubblewrap", inference_profile="review"),
            executable_sha256="a" * 64,
            connection=connection,
        ),
    )
    target = structural_fleet_plan_target(plan)
    capability = capability_snapshot(OperationAction.EXECUTE_PLAN, target)
    admitted = RunAdmittedEvent(
        event_type=FleetRunEventType.RUN_ADMITTED,
        sequence=1,
        fleet_run_id="read-copy-run",
        target=target,
        capability=capability,
        repository_scope=fleet_repository_scope({repository.repository_id}),
        targets=(repository,),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )
    state = baseline_for(repository)
    effect = RepositoryEffectIdentity(
        operation_id="read-copy-operation",
        repository=repository,
        capability=capability,
        target=target,
        origin=DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT),
    )
    reconciliation = PartiallyAppliedRepositoryReconciliation(
        outcome=ReconciliationOutcome.PARTIALLY_APPLIED,
        reason="retained partial effect",
        baseline=state,
        current=state,
    )
    return JournalReadView.from_entries(
        (
            RunEventCommitted(1, "read-copy-session", admitted),
            EffectReconciliationCommitted(
                2,
                "read-copy-session",
                RepositoryEffectReconciliationEvidence(
                    effect=effect, reconciliation=reconciliation
                ),
            ),
            EffectAcknowledgementCommitted(
                3,
                "read-copy-session",
                RepositoryEffectAcknowledgementEvidence(
                    effect=effect, reconciliation=reconciliation, note="operator acknowledged"
                ),
            ),
        ),
        "read-copy-session",
    )


def _read_target(view: JournalReadView, surface: str) -> FleetPlanTarget:
    if surface == "active-run":
        run = view.active_run()
        assert run is not None
        target = run.target
    elif surface == "event":
        event = view.run_events("read-copy-run").events[0]
        assert isinstance(event, RunAdmittedEvent)
        target = event.target
    elif surface == "event-state":
        target = view.run_events("read-copy-run").run.target
    elif surface == "reconciliation":
        reconciliation = view.latest_effect_reconciliation("read-copy-operation")
        assert reconciliation is not None
        target = reconciliation.effect.target
    else:
        assert surface == "acknowledgement"
        acknowledgement = view.effect_acknowledgement("read-copy-operation")
        assert acknowledgement is not None
        target = acknowledgement.effect.target
    assert isinstance(target, FleetPlanTarget)
    return target


@pytest.mark.parametrize(
    "surface", ("active-run", "event", "event-state", "reconciliation", "acknowledgement")
)
def test_nested_json_mutation_in_public_readback_cannot_change_verified_cut(
    journal_view: JournalReadView, surface: str
) -> None:
    before = journal_view.run_events("read-copy-run").model_dump(mode="json")
    returned = _read_target(journal_view, surface)
    assert returned.plan.native_agent is not None
    assert returned.plan.native_agent.connection is not None
    custom = returned.plan.native_agent.connection.selection.parameters["custom"]
    assert isinstance(custom, dict)
    items = custom["items"]
    assert isinstance(items, list)
    items.append("caller mutation")

    assert journal_view.run_events("read-copy-run").model_dump(mode="json") == before
    reread = _read_target(journal_view, surface)
    assert reread.plan.native_agent is not None
    assert reread.plan.native_agent.connection is not None
    assert reread.plan.native_agent.connection.selection.parameters == {
        "custom": {"items": ["recorded"]}
    }
