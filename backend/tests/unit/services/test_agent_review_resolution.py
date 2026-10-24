import json
from dataclasses import replace

import pytest

from oh_goon.core.agent_review_plan import agent_review_phases
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_providers import (
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_review import (
    MAX_REVIEW_BYTES,
    AgentAdjudicationRecord,
    AgentReviewRecord,
    accept_review,
    review_scope,
)
from oh_goon.core.models.fleet_observation import (
    NativeAgentObservation,
    NativeServiceExecutionEvidence,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedLocalSearchAgentStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanStandardIsolationEvidence,
    PlanStepAttemptEvidence,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CancelledFleetRun,
    CompletedFleetRun,
    FailedFleetRun,
    FleetRunDetail,
    FleetRunFailure,
    FleetRunProgress,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunStatus, OperationAction, Verdict
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.agent_review_execution import accept_agent_phase_output
from oh_goon.services.fleet_plan_repair import AgentReviewRepairCampaign, completed_phase_steps
from tests.factories.agent_review import agent_review_case
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for
from tests.factories.shipped_templates import PLAN_TEMPLATES


def test_shipped_loop_freezes_occurrences_and_shares_one_candidate(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    assert len(plan.steps) == 9
    assert len(deliveries) == 6
    assert len(agent_review_phases(plan)["review"]) == 4
    assert len({id(h.source.repository) for h in deliveries.values()}) == 1
    assert {h.requested_effects.recipients for h in deliveries.values()} == {("configured_model",)}
    before = build_execution_lock(plan)
    edges = tuple(edge.model_copy(update={"phase": None}) for edge in plan.imports)
    assert build_execution_lock(plan.model_copy(update={"imports": edges})).digest != before.digest
    campaign = AgentReviewRepairCampaign(
        plan,
        deliveries,
        resolver=resolver,
        plan_path=path,
        capture_options=options,
        passes=5,
        write_roots=("code.txt",),
    )
    assert len(campaign.plan.steps) == 4
    assert all(s.harness == "provider-review" for s in campaign.plan.steps)


def test_bound_skills_and_public_inputs_follow_native_projection(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    names = {item.name for item in resolver.input_definitions(path)}
    assert names == {
        "repo",
        "goal",
        "acceptance",
        "adjudication_skill",
        "repair_skill",
        "regression_command",
        "verification_command",
    }
    connection = options.pop("connection")
    selections = []

    def select_connection():
        selections.append(connection)
        return connection

    plan, deliveries, _ = resolver.resolve_frozen_agents(
        path, connection=select_connection, **options
    )
    assert selections == [connection]
    assert len({h.source.authored_skill_reference for h in deliveries.values()}) == 6
    assert {s.connection.profile for s in plan.steps if s.kind == "agent"} == {"review"}


def test_native_apply_argument_bounds_are_checked_before_the_first_phase(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    with pytest.raises(FleetPlanInvalid, match="command argument contract"):
        AgentReviewRepairCampaign(
            plan,
            deliveries,
            resolver=resolver,
            plan_path=path,
            capture_options=options,
            passes=5,
            write_roots=tuple(f"src/root-{i}" for i in range(32)),
        )


@pytest.mark.parametrize("attack", ["missing", "overlap", "order", "wrong_role"])
def test_invalid_phase_membership_is_refused_before_execution(tmp_path, attack):
    resolver, path, _, options = agent_review_case(tmp_path)
    plan, _, _ = resolver.resolve_frozen_agents(path, **options)
    edges = list(plan.imports)
    if attack == "missing":
        edges[-1] = edges[-1].model_copy(update={"phase": None})
    elif attack == "overlap":
        edges[0] = edges[0].model_copy(update={"phase": "review"})
    elif attack == "order":
        edges[-1] = edges[-1].model_copy(update={"phase": "regression"})
        edges[-2] = edges[-2].model_copy(update={"phase": "verification"})
    else:
        edges[-1] = edges[-1].model_copy(update={"phase": "review"})
    with pytest.raises(FleetPlanInvalid):
        agent_review_phases(plan.model_copy(update={"imports": tuple(edges)}))


def test_review_record_requires_source_bound_evidence_and_exact_occurrence(tmp_path):
    resolver, path, _, options = agent_review_case(tmp_path)
    _, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    occurrence, handoff = next(iter(deliveries.items()))
    member = next(m for m in handoff.source.repository.members if m.path == "code.txt")
    record = AgentReviewRecord(
        reviewer=occurrence,
        candidate_identity=handoff.source.repository.content_identity,
        reviewed_paths=["repo/" + member.path for member in handoff.source.repository.members],
        scope_identity=review_scope(handoff),
        skill_identity=handoff.source.skill.content_identity,
        verdict="FINDING",
        summary="broken source",
        findings=[
            dict(
                finding_id="f1",
                explanation="Repair the broken source",
                evidence=[dict(path="repo/code.txt", sha256=member.sha256, line=1)],
            )
        ],
    )
    assert accept_review(record.model_dump_json(), handoff, occurrence) == record
    with pytest.raises(FleetPlanInvalid):
        record.verify(handoff, "another-occurrence")
    with pytest.raises(FleetPlanInvalid):
        record.verify(
            replace(handoff, source=replace(handoff.source, repository_reference="local:other")),
            occurrence,
        )
    for attack in (
        dict(candidate_identity="stale"),
        dict(scope_identity="smaller"),
        dict(skill_identity="other"),
    ):
        with pytest.raises(FleetPlanInvalid):
            record.model_copy(update=attack).verify(handoff, occurrence)
    with pytest.raises(FleetPlanInvalid):
        accept_review(record.model_dump_json()[:-1] + ',"verdict":"CLEAN"}', handoff, occurrence)
    adjudication = AgentAdjudicationRecord(
        candidate_identity=record.candidate_identity,
        scope_identity=record.scope_identity,
        dispositions=[
            dict(
                reviewer=occurrence,
                finding_id="f1",
                decision="repair",
                rationale="fix within scope",
            )
        ],
        queue=[
            dict(
                queue_id="q1",
                findings=[dict(reviewer=occurrence, finding_id="f1")],
                rationale="one root",
                regression_expectation="reproduces defect",
            )
        ],
    )
    adjudication.verify((record,))
    with pytest.raises(FleetPlanInvalid):
        adjudication.model_copy(update={"dispositions": ()}).verify((record,))
    # Distinct findings survive identical titles and retain separate queue entries.
    second = record.findings[0].model_copy(update={"finding_id": "f2"})
    multiple = record.model_copy(update={"findings": (*record.findings, second)})
    disposition = adjudication.dispositions[0].model_copy(update={"finding_id": "f2"})
    group = adjudication.queue[0].model_copy(
        update={
            "queue_id": "q2",
            "findings": (
                adjudication.queue[0].findings[0].model_copy(update={"finding_id": "f2"}),
            ),
        }
    )
    queued = adjudication.model_copy(
        update={
            "dispositions": (*adjudication.dispositions, disposition),
            "queue": (*adjudication.queue, group),
        }
    )
    queued.verify((multiple,))
    with pytest.raises(FleetPlanInvalid, match="partition"):
        queued.model_copy(update={"queue": adjudication.queue}).verify((multiple,))


def test_same_native_workflow_keeps_phase_occurrences_when_implementation_changes(tmp_path):
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
    )
    resolver, path, _, options = agent_review_case(tmp_path, native_agent=selected)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
    changed = replace(
        selected, agent="opencode", definition=NativeAgentDefinition(executable="/native/opencode")
    )
    options["native_agent"] = changed
    another, another_deliveries, _ = resolver.resolve_frozen_agents(
        path, previous=deliveries, **options
    )
    assert agent_review_phases(plan) == agent_review_phases(another)
    assert tuple(deliveries) == tuple(another_deliveries)
    assert {key: value.handoff_identity for key, value in deliveries.items()} == {
        key: value.handoff_identity for key, value in another_deliveries.items()
    }
    assert {step.native_agent.agent for step in another.steps if step.kind == "agent"} == {
        "opencode"
    }
    assert plan.plan_id != another.plan_id
    campaign = AgentReviewRepairCampaign(
        another,
        another_deliveries,
        resolver=resolver,
        plan_path=path,
        capture_options=options,
        passes=3,
        write_roots=("code.txt",),
    )
    assert (
        tuple(step.step_id for step in campaign.plan.steps)
        == agent_review_phases(another)["review"]
    )
    assert all(step.role == "review" and step.harness == "native" for step in campaign.plan.steps)
    campaign._recapture(require_unchanged=True)
    assert all(
        step.native_agent.agent == "opencode"
        for step in campaign.original.steps
        if step.kind == "agent"
    )
    campaign.phase = "verification"
    verification = campaign._select_phase()
    assert all(step.kind != "agent" for step in verification.steps)
    assert verification.native_agent == another.native_agent


def test_native_workspace_mismatch_rejects_before_source_bytes(tmp_path, monkeypatch):
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
    )
    resolver, path, _, options = agent_review_case(tmp_path, native_agent=selected)
    other = tmp_path / "repos/other"
    other.mkdir()
    from unittest.mock import Mock

    read = Mock(side_effect=AssertionError("workspace mismatch reached byte acquisition"))
    monkeypatch.setattr("oh_goon.services.agent_authoring_sources.os.read", read)
    with pytest.raises(FleetPlanInvalid, match="explicit native workspace"):
        resolver.resolve_frozen_agents(path, **{**options, "repository": other})
    read.assert_not_called()


def test_codex_service_campaign_retains_read_only_reasoning_and_native_apply(tmp_path):
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="owned-service",
        definition=NativeAgentDefinition(executable="/native/codex"),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(tmp_path / "native-home"), model="gpt-6.1-sol", reasoning_effort="low"
            )
        ),
        executable_sha256="a" * 64,
    )
    resolver, path, _, options = agent_review_case(tmp_path, native_agent=selected)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    campaign = AgentReviewRepairCampaign(
        plan,
        handoffs,
        resolver=resolver,
        plan_path=path,
        capture_options=options,
        passes=2,
        write_roots=("code.txt", "regression.txt"),
    )
    assert {handoff.requested_effects.repository_access for handoff in handoffs.values()} == {
        "read_only"
    }
    assert {step.role for step in plan.steps if step.kind == "agent"} == {
        "review",
        "adjudication",
        "proposal",
    }
    assert all(step.native_agent == selected for step in campaign.plan.steps)
    campaign.phase = "repair"
    proposal, apply = campaign._select_phase().steps
    assert proposal.role == "proposal" and proposal.native_agent == selected
    assert isinstance(apply, ResolvedCommandStep)
    assert apply.argv.count("--write-root") == 2
    assert "code.txt" in apply.argv and "regression.txt" in apply.argv
    assert "@admitted" not in apply.argv and "@candidate" not in apply.argv
    assert campaign.candidate_identity in apply.argv
    for phase in ("regression", "verification"):
        campaign.phase = phase
        projected = campaign._select_phase()
        assert all(step.kind == "command" for step in projected.steps)
        assert projected.native_agent == selected
    campaign._recapture(require_unchanged=True)


@pytest.mark.parametrize(
    "returncode,cancelled,expected_stop",
    [(2, False, "review_incomplete"), (124, False, "timed_out"), (2, True, "cancelled")],
)
def test_service_daemon_shutdown_preserves_failed_preflight_timeout_and_caller_cancellation(
    tmp_path, returncode, cancelled, expected_stop
):
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="owned-service",
        definition=NativeAgentDefinition(executable="/native/codex"),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(tmp_path / "native-home"), model="gpt-6.1-sol", reasoning_effort="low"
            )
        ),
        executable_sha256="a" * 64,
    )
    resolver, path, repository, options = agent_review_case(tmp_path, native_agent=selected)
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    campaign = AgentReviewRepairCampaign(
        plan,
        handoffs,
        resolver=resolver,
        plan_path=path,
        capture_options=options,
        passes=2,
        write_roots=("code.txt",),
    )
    plan = campaign.plan
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=tuple(
            AgentStepDeliveryReference(
                step_id=step.step_id,
                handoff_identity=step.handoff_identity,
                delivery_sha256=step.delivery_sha256,
                reference_id="original-" + step.step_id,
            )
            for step in plan.steps
        ),
    )
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    first, *remaining = plan.steps
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        returncode=returncode,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="source",
        repository_state_after="source",
        egress=PlanEgressEvidence(mode="none"),
        supervisor=PlanExecutionSupervisorEvidence(
            process_group_cleanup_requested=True,
            cleanup_reason="cancel",
            final_process_group_quiescent=True,
            forced_termination=False,
        ),
    )
    failed = FailedPlanStepExecution(
        step_id=first.step_id,
        kind="agent",
        returncode=returncode,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="source",
        repository_state_after="source",
        attempts=(attempt,),
    )
    skipped = tuple(
        SkippedPlanStepExecution(
            step_id=step.step_id,
            kind="agent",
            reason="Prior phase Step failed",
            stdout_evidence=output,
            stderr_evidence=output,
            repository_state_before="source",
            repository_state_after="source",
        )
        for step in remaining
    )
    identity = RepositoryIdentity(path=repository)
    report = make_operation_report(
        identity,
        OperationAction.EXECUTE_PLAN,
        target=target,
        verdict=Verdict.FAILED,
        returncode=returncode,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(plan_id=plan.plan_id, steps=(failed, *skipped)),
        ),
    )
    now = utc_now()
    fields = dict(
        fleet_run_id="fleet-test",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(identity,),
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1),
    )
    run = (
        CancelledFleetRun(**fields, status=FleetRunStatus.CANCELLED, cancel_requested_at=now)
        if cancelled
        else FailedFleetRun(
            **fields,
            status=FleetRunStatus.FAILED,
            failure=FleetRunFailure.from_exception(
                FleetPlanInvalid("Native capability preflight failed"),
                stage=FleetRunFailureStage.EXECUTION,
            ),
        )
    )
    detail = FleetRunDetail(
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
        repository_admissions=repository_admissions_for(target, (identity,)),
    )
    assert campaign.advance(detail).value == expected_stop
    assert campaign.phase == "review"
    assert (repository / "code.txt").read_text() == "broken\n"


@pytest.mark.parametrize("attack", ["legacy", "missing_workspace", "missing_git_scope"])
def test_native_repair_rejects_unsupported_ingress_before_capture_or_run(tmp_path, attack):
    from unittest.mock import Mock

    from oh_goon.core.models.agent_providers import (
        AgentConnection,
        AgentModel,
        AgentProvider,
        AgentProvidersConfig,
    )
    from oh_goon.core.models.agent_repair import AgentRepairBudget
    from oh_goon.services.fleet_plans import FleetPlanService

    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="approved"),
        executable_sha256="a" * 64,
        connection=AgentConnection.select(
            AgentProvidersConfig(
                default_model="approved",
                providers={
                    "host": AgentProvider(
                        api="openai-chat-completions", base_url="http://127.0.0.1:12345/v1"
                    )
                },
                models={"approved": AgentModel(provider="host", model="test")},
            )
        ),
    )
    resolver, path, repo, options = agent_review_case(tmp_path, native_agent=selected)
    resolver.resolve_frozen_agents = Mock(
        side_effect=AssertionError("unsupported ingress reached capture")
    )
    runs = Mock()
    service = FleetPlanService(
        resolver, runs, Mock(), Mock(), Mock(), standard_local_fleet=True, content_store=Mock()
    )
    expected = {
        "legacy": "complete review campaign",
        "missing_workspace": "one explicit workspace",
        "missing_git_scope": "scoped Git",
    }[attack]
    with pytest.raises(FleetPlanInvalid, match=expected):
        service.repair(
            path,
            budget=AgentRepairBudget(max_attempts=2, timeout_seconds=30, step_timeout_seconds=10),
            repository_id="selected",
            inputs=options["inputs"],
            agent_repository_root=options["repository_root"],
            agent_skill_root=options["skill_root"],
            allowed_agent_acceptance=options["allowed_acceptance"],
            native_agent=selected,
            native_workspace=None if attack == "missing_workspace" else repo,
            review_write_roots=None if attack == "legacy" else ("code.txt",),
        )
    resolver.resolve_frozen_agents.assert_not_called()
    runs.start_prepared_plan.assert_not_called()


def test_native_git_input_uses_the_original_injected_observation_runner(tmp_path):
    from unittest.mock import Mock

    import yaml

    from oh_goon.engine.command import CommandResult
    from oh_goon.engine.git import GitService

    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
    )
    resolver, path, repo, options = agent_review_case(tmp_path, native_agent=selected)
    source = yaml.safe_load(path.read_text())
    source["goons"][0] = {
        "import": source["goons"][0],
        "with": {"goal": {"from": {"git": "branch"}}},
    }
    path.write_text(yaml.safe_dump(source))
    observe = Mock(
        return_value=CommandResult(0, "# branch.oid (initial)\0# branch.head scoped-goal\0", "")
    )
    git = GitService(
        command_runner=Mock(side_effect=AssertionError("Git observation escaped its native scope")),
        observation_runner=observe,
    )
    _, handoffs, _ = resolver.resolve_frozen_agents(path, git_service=git, **options)
    assert {handoff.task for handoff in handoffs.values()} == {"scoped-goal"}
    observe.assert_called_once()


@pytest.mark.parametrize("agent", ["pi", "opencode"])
def test_native_configured_model_marker_freezes_one_literal_recipient_before_capture(
    tmp_path, agent
):
    from oh_goon.core.models.agent_providers import (
        AgentConnection,
        AgentModel,
        AgentProvider,
        AgentProvidersConfig,
    )

    connection = AgentConnection.select(
        AgentProvidersConfig(
            default_model="approved",
            providers={
                "company": AgentProvider(
                    api="openai-chat-completions", base_url="http://127.0.0.1:12345/v1"
                )
            },
            models={"approved": AgentModel(provider="company", model="native-model")},
        )
    )
    selected = ResolvedNativeAgent(
        agent=agent,
        profile_name="isolated",
        definition=NativeAgentDefinition(executable=f"/native/{agent}"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="approved"),
        executable_sha256="a" * 64,
        connection=connection,
    )
    resolver, path, _, options = agent_review_case(tmp_path, native_agent=selected)
    authored = tmp_path / "native-goons/agents/review/goon.yml"
    assert "recipients: [configured_model]" in authored.read_text()
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    assert {handoff.requested_effects.recipients for handoff in handoffs.values()} == {("company",)}
    assert "recipients: [configured_model]" in authored.read_text()
    again, reused, _ = resolver.resolve_frozen_agents(path, previous=handoffs, **options)
    assert reused == handoffs and again == plan


def test_native_wrong_literal_recipient_rejects_before_source_capture(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from oh_goon.core.models.agent_providers import (
        AgentConnection,
        AgentModel,
        AgentProvider,
        AgentProvidersConfig,
    )

    connection = AgentConnection.select(
        AgentProvidersConfig(
            default_model="approved",
            providers={
                "company": AgentProvider(
                    api="openai-chat-completions", base_url="http://127.0.0.1:12345/v1"
                )
            },
            models={"approved": AgentModel(provider="company", model="native-model")},
        )
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="approved"),
        executable_sha256="a" * 64,
        connection=connection,
    )
    resolver, path, _, options = agent_review_case(tmp_path, native_agent=selected)
    authored = tmp_path / "native-goons/agents/review/goon.yml"
    authored.write_text(authored.read_text().replace("[configured_model]", "[wrong-company]"))
    capture = Mock(side_effect=AssertionError("Wrong disclosure reached source-byte capture"))
    monkeypatch.setattr("oh_goon.services.agent_authoring_sources.capture_agent_sources", capture)
    with pytest.raises(FleetPlanInvalid, match="disclosure differs"):
        resolver.resolve_frozen_agents(path, **options)
    capture.assert_not_called()


@pytest.mark.parametrize(
    "plan_key,role,access",
    [("agents/native-coding", "report", "write"), ("agents/native-review", "review", "read_only")],
)
def test_shipped_native_plan_swaps_pi_opencode_without_reauthoring_and_keeps_verification_profile(
    tmp_path, plan_key, role, access
):
    from oh_goon.core.models.agent_providers import (
        AgentConnection,
        AgentModel,
        AgentProvider,
        AgentProvidersConfig,
    )
    from oh_goon.core.models.fleet_plan import ResolvedFleetPlan

    resolver, path, repo, options = agent_review_case(tmp_path, plan_template=plan_key)
    options.pop("connection")
    options["inputs"] = {"repo": "local:repo", "skill": "local:skills/repair"}
    if role == "report":
        options["inputs"]["verification_command"] = "check"
    acceptance = "native-coding" if role == "report" else "agentic-review"
    options["allowed_acceptance"] = (acceptance,)
    options["repository"] = repo
    connection = AgentConnection.select(
        AgentProvidersConfig(
            default_model="approved",
            providers={
                "company": AgentProvider(
                    api="openai-chat-completions", base_url="http://127.0.0.1:12345/v1"
                )
            },
            models={"approved": AgentModel(provider="company", model="native-model")},
        )
    )
    selection = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated-managed",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="approved"),
        executable_sha256="a" * 64,
        connection=connection,
    )
    options["native_agent"] = selection
    authored = path.read_bytes()
    assert authored.decode() == PLAN_TEMPLATES.source(plan_key).content
    first, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    step = first.steps[0]
    assert step.harness == "native" and step.role == role
    assert handoffs[step.step_id].requested_effects.repository_access == access
    assert handoffs[step.step_id].requested_effects.recipients == ("company",)
    assert handoffs[step.step_id].source.acceptance_profile == acceptance
    options["native_agent"] = replace(
        selection, agent="opencode", definition=NativeAgentDefinition(executable="/native/opencode")
    )
    second, again, _ = resolver.resolve_frozen_agents(path, previous=handoffs, **options)
    assert again == handoffs and path.read_bytes() == authored
    assert first.plan_id != second.plan_id
    assert second.native_agent.agent == "opencode"
    if role == "report":
        assert len(second.steps) == 2 and second.steps[1].argv == ("./check",)
        verification = ResolvedFleetPlan.create(
            name=second.name,
            description=second.description,
            steps=(second.steps[1],),
            execution=second.execution,
            native_agent=second.native_agent,
        )
        assert verification.native_agent == options["native_agent"]
        ordinary = ResolvedFleetPlan.create(
            name=verification.name,
            description=verification.description,
            steps=verification.steps,
            execution=verification.execution,
        )
        assert build_execution_lock(verification).digest != build_execution_lock(ordinary).digest
    else:
        assert len(second.steps) == 1


@pytest.mark.parametrize("attack", ["wrong_role", "local", "interactive"])
def test_native_phase_constraints_fail_before_first_phase(tmp_path, attack):
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
    )
    if attack == "local":
        selected = replace(selected, profile=NativeAgentProfile(backend="local"))
    elif attack == "interactive":
        selected = replace(selected, interactive=True)
    resolver, path, _, options = agent_review_case(tmp_path, native_agent=selected)
    with pytest.raises(FleetPlanInvalid):
        plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
        if attack == "wrong_role":
            first, *rest = plan.steps
            plan = plan.model_copy(
                update={"steps": (first.model_copy(update={"role": "report"}), *rest)}
            )
        AgentReviewRepairCampaign(
            plan,
            deliveries,
            resolver=resolver,
            plan_path=path,
            capture_options=options,
            passes=3,
            write_roots=("code.txt",),
        )


def test_review_context_retains_source_and_original_skill_for_adjudication(tmp_path):
    resolver, path, repo, options = agent_review_case(tmp_path)
    (tmp_path / "skills/adjudicate/SKILL.md").write_text("# Distinct adjudication guidance\n")
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    campaign = AgentReviewRepairCampaign(
        plan,
        handoffs,
        resolver=resolver,
        plan_path=path,
        capture_options=options,
        passes=2,
        write_roots=("code.txt",),
    )
    admitted = []
    for step in campaign.plan.steps:
        handoff = handoffs[step.step_id]
        members = handoff.source.repository.members
        full_paths = ["repo/" + member.path for member in members]
        source = next(member for member in members if member.path == "code.txt")
        wire = dict(
            schema="oh-goon.agent-review/v1",
            reviewer=step.step_id,
            candidate_identity=handoff.source.repository.content_identity,
            scope_identity=review_scope(handoff),
            skill_identity=handoff.source.skill.content_identity,
            verdict="FINDING",
            summary="Reproduced source defect",
            reviewed_paths={"complete_scope": review_scope(handoff)},
            findings=[
                dict(
                    finding_id="f1",
                    explanation="Repair the broken source",
                    evidence=[dict(path="repo/code.txt", sha256=source.sha256, line=1)],
                )
            ],
        )
        text = json.dumps(wire)
        assert len(text.encode()) < MAX_REVIEW_BYTES
        outcome = accept_agent_phase_output(
            step, handoff, StepOutcome(argv=("review",), returncode=0, stdout=text)
        )
        record = outcome.observations[0].record
        assert record.reviewed_paths == tuple(full_paths)
        assert record.model_dump(mode="json")["reviewed_paths"] == full_paths
        assert json.loads(outcome.stdout) == wire
        assert accept_review(outcome.stdout, handoff, step.step_id) == record
        admitted.append(record)
    campaign.reviews = tuple(admitted)
    campaign.phase = "adjudicate"
    phase = campaign._select_phase()
    step = phase.steps[0]
    context = json.loads(step.review_context)
    assert len(step.review_context.encode()) < MAX_REVIEW_BYTES
    assert len(context["reviews"]) == 4
    for record, compact in zip(admitted, context["reviews"], strict=True):
        assert compact["reviewed_paths"] == {"complete_scope": record.scope_identity}
        assert compact["findings"] == record.model_dump(mode="json")["findings"]
        assert compact["skill_identity"] == record.skill_identity
    handoff = handoffs[step.step_id]
    assert all(
        record.skill_identity != handoff.source.skill.content_identity for record in admitted
    )
    finding_refs = [{"reviewer": record.reviewer, "finding_id": "f1"} for record in admitted]
    adjudication = dict(
        schema="oh-goon.agent-adjudication/v1",
        candidate_identity=campaign.candidate_identity,
        scope_identity=campaign.scope_identity,
        dispositions=[
            {**ref, "decision": "repair", "rationale": "One observed defect"}
            for ref in finding_refs
        ],
        queue=[
            dict(
                queue_id="q1",
                findings=finding_refs,
                rationale="One causal root",
                regression_expectation="Reproduce the source defect",
            )
        ],
    )
    accepted = accept_agent_phase_output(
        step,
        handoff,
        StepOutcome(argv=("adjudicate",), returncode=0, stdout=json.dumps(adjudication)),
    )
    assert accepted.observations[0].record.model_dump(mode="json") == adjudication
    assert (repo / "code.txt").read_text() == "broken\n"


@pytest.mark.parametrize(
    "quiescent,cleanup,shutdown",
    [
        (None, None, None),
        (False, None, None),
        (True, None, None),
        (True, "not_recorded", None),
        (True, "unverified", None),
        (True, "verified", None),
        (True, None, "qualified"),
        (False, None, "qualified"),
        (True, "unverified", "qualified"),
        (True, None, "missing_execution"),
        (True, None, "wrong_profile"),
        (True, None, "wrong_implementation"),
        (True, None, "wrong_occurrence"),
        (True, None, "wrong_role"),
        (True, None, "wrong_kind"),
        (True, None, "unsettled"),
        (True, None, "failed"),
        (True, None, "write"),
        (True, None, "timeout"),
    ],
)
def test_structured_phase_requires_affirmative_boundary_cleanup(
    tmp_path, quiescent, cleanup, shutdown
):
    expected = ResolvedCommandStep(step_id="review", argv=("true",))
    observations = ()
    if shutdown is not None:
        selected = ResolvedNativeAgent(
            agent="codex",
            profile_name="owned-service",
            definition=NativeAgentDefinition(executable="/native/codex"),
            profile=NativeAgentProfile(
                codex_service=CodexServiceConnection(
                    home="/private/native", model="gpt-6.1-sol", reasoning_effort="low"
                )
            ),
            executable_sha256="a" * 64,
        )
        expected = ResolvedLocalSearchAgentStep(
            step_id="review",
            argv=("python",),
            harness="native",
            role="review",
            delivery_sha256="b" * 64,
            handoff_identity="handoff",
            source_step_id="review",
            native_agent=selected,
        )
        execution = NativeServiceExecutionEvidence(
            configuration_id="original-service-configuration",
            permission_profile_id="original-native-profile",
            native_version="0.163.0",
            repository_access="write" if shutdown == "write" else "read_only",
            disabled_features=(),
        )
        observations = (
            NativeAgentObservation(
                agent="codex",
                implementation_identity="codex:sha256:"
                + ("c" if shutdown == "wrong_implementation" else "a") * 64,
                profile="other" if shutdown == "wrong_profile" else selected.profile_name,
                input_identity="original-input",
                occurrence="other" if shutdown == "wrong_occurrence" else "review",
                source_identity="original-source",
                role="proposal" if shutdown == "wrong_role" else "review",
                events=(),
                native_event_count=1,
                settled=shutdown != "unsettled",
                failed=shutdown == "failed",
                execution=None if shutdown == "missing_execution" else execution,
            ),
        )
    plan = ResolvedFleetPlan.create(
        name="protected-phase",
        description="",
        requirements=(),
        execution=MaintenanceExecutionPolicy(),
        steps=(expected,),
        native_agent=selected if shutdown is not None else None,
    )
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    supervisor = PlanExecutionSupervisorEvidence.model_validate(
        {
            "process_group_cleanup_requested": True,
            "cleanup_reason": "output_cleanup"
            if shutdown is None
            else "timeout"
            if shutdown == "timeout"
            else "cancel",
            **(
                {"final_process_group_quiescent": quiescent, "forced_termination": True}
                if quiescent is not None
                else {}
            ),
        }
    )
    # Existing retained receipts with no terminal observation stay unknown on readback.
    supervisor = PlanExecutionSupervisorEvidence.model_validate_json(supervisor.model_dump_json())
    assert supervisor.final_process_group_quiescent is quiescent
    if quiescent is None:
        assert "final_process_group_quiescent" not in supervisor.model_dump()
        assert supervisor.forced_termination is None
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        returncode=0,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="source",
        repository_state_after="source",
        egress=PlanEgressEvidence(mode="none"),
        supervisor=supervisor,
        standard_isolation=PlanStandardIsolationEvidence(
            backend=PlanIsolationBackendEvidence(
                backend_id="bubblewrap",
                configuration_id="fixture-boundary",
                kernel_boundary="shared_host_kernel",
                remote_execution=False,
                experimental=False,
            ),
            workspace_read_only=True,
            broker_socket_mounted=False,
            resource_owner="none",
            filesystem_cleanup=cleanup,
        )
        if cleanup is not None
        else None,
    )
    step = CompletedPlanStepExecution(
        step_id="review",
        kind="agent" if shutdown is not None and shutdown != "wrong_kind" else "command",
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="source",
        repository_state_after="source",
        attempts=(attempt,),
        observations=observations,
    )
    target = (
        FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_deliveries=(
                AgentStepDeliveryReference(
                    step_id="review",
                    handoff_identity="handoff",
                    delivery_sha256="b" * 64,
                    reference_id="original-delivery",
                ),
            ),
        )
        if shutdown is not None
        else structural_fleet_plan_target(plan)
    )
    repository = RepositoryIdentity(path=tmp_path)
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(plan_id=plan.plan_id, steps=(step,)),
        ),
    )
    now = utc_now()
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="fleet-test",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    detail = FleetRunDetail(
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )
    if quiescent is True and cleanup in (None, "verified") and shutdown in (None, "qualified"):
        assert completed_phase_steps(detail, plan) == (step,)
    else:
        with pytest.raises(FleetPlanInvalid, match="complete successful native attempt evidence"):
            completed_phase_steps(detail, plan)
