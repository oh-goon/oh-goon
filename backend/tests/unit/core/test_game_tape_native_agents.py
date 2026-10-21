from __future__ import annotations

import hashlib

from oh_goon.core.game_tape_step_projection import project_steps
from oh_goon.core.models.fleet_observation import AgentHarnessObservation, NativeAgentObservation
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution, PlanOutputEvidence
from oh_goon.core.models.game_tape import GameTapeStepExecution
from oh_goon.core.vocabulary import GameTapeStatus


def test_durable_native_claims_remain_step_local_and_cannot_overrule_process_failure() -> None:
    planned = (
        ResolvedCommandStep(step_id="agent", argv=("pi",), timeout_seconds=10),
        ResolvedCommandStep(step_id="verification", argv=("pytest",), timeout_seconds=10),
    )
    observation = NativeAgentObservation(
        agent="pi",
        implementation_identity="pi:sha256:" + "1" * 64,
        profile="isolated-managed",
        input_identity="sha256:" + "2" * 64,
        occurrence="agent",
        source_identity="sha256:" + "3" * 64,
        role="report",
        events=(AgentHarnessObservation(kind="context", summary="pi: auto_compaction_end"),),
        native_event_count=6140,
        omitted_observations=6012,
        settled=True,
        failed=False,
    )
    output = PlanOutputEvidence(sha256=hashlib.sha256(b"").hexdigest(), byte_count=0, line_count=0)
    execution = FailedPlanStepExecution(
        step_id="agent",
        kind="local_search_agent",
        argv=("pi",),
        returncode=17,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="before",
        repository_state_after="after",
        observations=(observation,),
    )
    # Read the same durable evidence representation used by Run detail.
    retained = FailedPlanStepExecution.model_validate_json(execution.model_dump_json())
    agent, verification = project_steps(
        "native-run",
        "repository",
        planned,
        (retained,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="OPERATION_FAILED",
    )
    assert agent.status is GameTapeStatus.FAILED
    assert agent.exit_code == 17
    assert agent.failure is not None and agent.failure.exit_code == 17
    assert agent.native_agent_observations == (observation,)
    assert agent.native_agent_observations[0].authority == "UNTRUSTED_HARNESS_CLAIM"
    assert agent.native_agent_observations[0].native_event_count == 6140
    assert agent.native_agent_observations[0].omitted_observations == 6012
    assert verification.native_agent_observations == ()
    assert verification.status is GameTapeStatus.NOT_RUN
    assert (
        GameTapeStepExecution.model_validate_json(
            agent.model_dump_json(exclude_computed_fields=True)
        )
        == agent
    )
