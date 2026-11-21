"""Stopped campaign inspection keeps historical bytes and current subject separate."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oh_goon.core.errors import FleetPlanInvalid, ReviewedRepositorySourceStale
from oh_goon.core.models.agent_continuation import AgentContinuationSourceInspection
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputType
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations import source_snapshot
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.source_snapshot import source_snapshot_identity
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services import fleet_plan_continuation_inspection as inspection
from oh_goon.services.agent_authoring_sources import (
    capture_agent_sources,
    require_native_agent_source_path_privacy,
)
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.local_state import local_state
from tests.factories.run_admission import repository_admissions_for


@dataclass
class InspectionCase:
    repository: RepositoryIdentity
    repositories: RepositoryService
    previous: FleetRunDetail
    store: FleetContentStore
    handoff: FrozenAgentStepHandoff

    def inspect(self, **options: Any) -> AgentContinuationSourceInspection:
        return inspection.inspect_continuation_source(
            repositories=self.repositories,
            repository_id=self.repository.repository_id,
            previous=self.previous,
            content_store=self.store,
            git_service=options.pop("git_service", None),
            **options,
        )


@pytest.fixture
def case(tmp_path: Path) -> InspectionCase:
    repository_path = tmp_path / "repositories/repo"
    repository_path.mkdir(parents=True)
    (repository_path / "main.py").write_text("print('before')\n")
    skill = tmp_path / "skills/review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\n")
    source = capture_agent_sources(
        (
            PlanInputDefinition(name="repo", input_type=PlanInputType.REPOSITORY_REFERENCE),
            PlanInputDefinition(name="skill", input_type=PlanInputType.SKILL_REFERENCE),
            PlanInputDefinition(name="acceptance", input_type=PlanInputType.ACCEPTANCE_PROFILE),
        ),
        {"repo": "local:repo", "skill": "local:review", "acceptance": "review"},
        repository_root=tmp_path / "repositories",
        skill_root=tmp_path / "skills",
        allowed_acceptance=("review",),
        plan_source_sha256="a" * 64,
        plan_graph_identity="inspection-fixture-graph",
    )
    handoff = FrozenAgentStepHandoff(
        source=source,
        owner_source_sha256="a" * 64,
        step_id="review",
        harness="local-search",
        task="Review the candidate",
    )
    packet = handoff.encode_prework_delivery()
    delivery_sha256 = hashlib.sha256(packet).hexdigest()
    repository = RepositoryIdentity(path=repository_path)
    store = FleetContentStore(tmp_path / "content")
    retained = store.put_frozen_bytes(packet, owner_repository_id=repository.repository_id)
    plan = ResolvedFleetPlan.create(
        name="Inspection fixture",
        description="Retained original candidate",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedAgentStep.model_validate(
                {
                    "step_id": handoff.step_id,
                    "source_step_id": handoff.step_id,
                    "timeout_seconds": 30,
                    "workload": {"harness": "local-search"},
                    "handoff_identity": handoff.handoff_identity,
                    "delivery_sha256": delivery_sha256,
                }
            ),
        ),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="a" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=handoff.step_id,
                handoff_identity=handoff.handoff_identity,
                delivery_sha256=delivery_sha256,
                reference_id=retained.reference_id,
            ),
        ),
    )
    now = datetime(2026, 11, 21, tzinfo=UTC)
    previous = FleetRunDetail(
        run=CompletedFleetRun(
            fleet_run_id="original-candidate-run",
            status=FleetRunStatus.COMPLETED,
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            repository_scope=fleet_repository_scope({repository.repository_id}),
            targets=(repository,),
            accepted_at=now,
            started_at=now,
            finished_at=now,
            progress=FleetRunProgress(total=1, succeeded=1, failed=0),
        ),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )
    state = local_state(LocalDatabase(tmp_path / "state.sqlite3"))
    state.add_location(repository.path)
    repositories = RepositoryService(state, RepositoryDiscoveryService())
    return InspectionCase(repository, repositories, previous, store, handoff)


@pytest.mark.process
def test_native_diff_compares_retained_bytes_with_current_registered_non_git_source(
    case: InspectionCase,
) -> None:
    original = case.handoff.source.repository.content_identity
    previous = case.previous.model_dump_json()
    old_subject = repository_subject_instance_id(case.repository.path)
    (case.repository.path / "main.py").write_text("print('after')\n")
    (case.repository.path / "added.py").write_text("new_member = True\n")
    (case.repository.path / "__pycache__").mkdir()
    (case.repository.path / "__pycache__/main.pyc").write_bytes(b"cache-only-sentinel")

    observed = case.inspect()

    assert not (case.repository.path / ".git").exists()
    assert observed.recorded_candidate_identity == original
    assert observed.repository_instance_id != old_subject
    assert observed.repository_instance_id == repository_subject_instance_id(case.repository.path)
    assert observed.source_authority.subject_instance_id == observed.repository_instance_id
    assert (
        observed.source_authority.source_snapshot.sha256
        == source_snapshot_identity(case.repository.path).sha256
    )
    assert observed.diff.availability == "available"
    assert "-print('before')" in observed.diff.text
    assert "+print('after')" in observed.diff.text
    assert "added.py" in observed.diff.text
    assert "cache-only-sentinel" not in observed.diff.text
    assert observed.diff.omitted_bytes == 0
    assert case.previous.model_dump_json() == previous


def test_stale_current_subject_is_rejected_before_source_capture(
    case: InspectionCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = repository_subject_instance_id(case.repository.path)
    (case.repository.path / "added.py").write_text("changed\n")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Stale current subject must fail before source capture")

    monkeypatch.setattr(inspection, "snapshot_source_directory", forbidden)
    with pytest.raises(ReviewedRepositorySourceStale, match="subject changed"):
        case.inspect(expected_repository_instance_id=expected)


@pytest.mark.process
def test_compiler_import_occurrence_uses_original_source_step_and_rejects_another_owner(
    case: InspectionCase, tmp_path: Path
) -> None:
    goon = tmp_path / "catalog/agents/review"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# agents/review\n")
    (goon / "goon.yml").write_text(
        "inputs:\n"
        "  repo: {type: repository_reference}\n"
        "  skill: {type: skill_reference}\n"
        "  acceptance: {type: acceptance_profile}\n"
        "steps:\n- id: review\n  uses: agent\n  harness: local-search\n  task: 'search:main'\n"
    )
    authored = tmp_path / "plan.yml"
    authored.write_text(
        "inputs: [repo, skill, acceptance]\ngoons:\n- import: agents/review\n  as: scoped\n"
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "catalog"))
    plan, handoffs, source_sha = resolver.resolve_frozen_agents(
        authored,
        inputs={"repo": "local:repo", "skill": "local:review", "acceptance": "review"},
        repository_root=tmp_path / "repositories",
        skill_root=tmp_path / "skills",
        allowed_acceptance=("review",),
    )
    step = plan.steps[0]
    assert isinstance(step, ResolvedAgentStep)
    handoff = handoffs[step.step_id]
    assert step.step_id != handoff.step_id
    assert step.source_step_id == handoff.step_id
    retained = case.store.put_frozen_bytes(
        handoff.encode_prework_delivery(), owner_repository_id=case.repository.repository_id
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256=source_sha),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=step.step_id,
                handoff_identity=step.handoff_identity,
                delivery_sha256=step.delivery_sha256,
                reference_id=retained.reference_id,
            ),
        ),
    )
    case.previous = FleetRunDetail(
        run=case.previous.run.model_copy(update={"target": target}),
        execution=case.previous.execution,
        repository_admissions=repository_admissions_for(target, (case.repository,)),
    )
    observed = case.inspect()
    assert observed.recorded_candidate_identity == handoff.source.repository.content_identity
    assert observed.diff.availability == "available"
    assert observed.diff.text == ""

    forged_step = step.model_copy(update={"source_step_id": "different-owner"})
    forged_plan = plan.model_copy(update={"steps": (forged_step,)})
    forged_target = target.model_copy(update={"plan": forged_plan})
    case.previous = case.previous.model_copy(
        update={"run": case.previous.run.model_copy(update={"target": forged_target})}
    )
    with pytest.raises(FleetPlanInvalid, match="different resolved Step owner"):
        case.inspect()


def test_same_bytes_replacement_during_capture_cannot_become_current_authority(
    case: InspectionCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    native_capture = inspection.snapshot_source_directory

    def replace_subject(source: Path, destination: Path, **options: Any):
        captured = native_capture(source, destination, **options)
        old = tmp_path / "replaced-repository"
        source.rename(old)
        shutil.copytree(old, source)
        return captured

    monkeypatch.setattr(inspection, "snapshot_source_directory", replace_subject)
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        case.inspect()


@pytest.mark.parametrize("foreign_reference", [False, True])
def test_inaccessible_original_evidence_never_becomes_an_empty_diff(
    case: InspectionCase, monkeypatch: pytest.MonkeyPatch, foreign_reference: bool
) -> None:
    target = case.previous.run.target
    assert isinstance(target, FleetPlanTarget)
    reference = target.agent_deliveries[0]
    if foreign_reference:
        foreign = RepositoryIdentity(path=case.repository.path.parent / "other")
        stored = case.store.put_frozen_bytes(
            case.handoff.encode_prework_delivery(), owner_repository_id=foreign.repository_id
        )
        target = target.model_copy(
            update={
                "agent_deliveries": (
                    reference.model_copy(update={"reference_id": stored.reference_id}),
                )
            }
        )
        case.previous = case.previous.model_copy(
            update={"run": case.previous.run.model_copy(update={"target": target})}
        )
    else:
        case.store.release_reference(
            reference.reference_id, owner_repository_id=case.repository.repository_id
        )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Unavailable baseline must fail before current capture")

    monkeypatch.setattr(inspection, "snapshot_source_directory", forbidden)
    with pytest.raises(FleetPlanInvalid, match="unavailable"):
        case.inspect()


def test_missing_native_git_preserves_source_and_reports_unavailable_diff(
    case: InspectionCase, tmp_path: Path
) -> None:
    observed = case.inspect(git_service=GitService(executable=str(tmp_path / "missing-git")))
    assert observed.source_authority.source_snapshot.byte_count > 0
    assert observed.diff.availability == "unavailable"
    assert observed.diff.text == ""
    assert "no absence of changes" in (observed.diff.reason or "")


def test_native_git_failure_is_unavailable_even_if_it_emits_text(
    case: InspectionCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        inspection,
        "run_command",
        lambda *_args, **_kwargs: CommandResult(2, "not-an-accepted-diff", "refused"),
    )
    observed = case.inspect()
    assert observed.diff.availability == "unavailable"
    assert observed.diff.text == ""


@pytest.mark.process
def test_temp_capture_diff_uses_native_git_without_entering_agent_workspace_runner(
    case: InspectionCase,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Agent workspace isolation cannot own a trusted temporary capture comparison")

    (case.repository.path / "main.py").write_text("print('after')\n")
    git = GitService(command_runner=forbidden, observation_runner=forbidden)
    observed = case.inspect(git_service=git)
    assert observed.diff.availability == "available"
    assert "-print('before')" in observed.diff.text
    assert "+print('after')" in observed.diff.text


@pytest.mark.process
def test_large_native_diff_reports_exact_stream_size_and_explicit_unknown_omissions(
    case: InspectionCase,
) -> None:
    (case.repository.path / "main.py").write_text("after = 1\n" * 12000)
    observed = case.inspect()
    assert observed.diff.availability == "available"
    assert observed.diff.truncated
    assert len(observed.diff.text.encode("utf-8")) <= 65536
    assert observed.diff.byte_count is not None and observed.diff.byte_count > 65536
    assert observed.diff.omitted_bytes is None
    assert "complete diff is omitted" in (observed.diff.reason or "")


@pytest.mark.parametrize(
    "private_path",
    [
        ".codex/auth.json",
        ".opencode/config.json",
        "opencode.json",
        "opencode.jsonc",
        "docs/certification/answers/secret.json",
    ],
)
def test_private_state_is_rejected_before_current_source_bytes_are_identified(
    case: InspectionCase, monkeypatch: pytest.MonkeyPatch, private_path: str
) -> None:
    private = case.repository.path / private_path
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("private-byte-sentinel")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Private source must be rejected before Hashmarks opens current member bytes")

    monkeypatch.setattr(source_snapshot, "_identify_source", forbidden)
    with pytest.raises(FleetPlanInvalid, match="native authentication|grader-only"):
        case.inspect()


def test_private_symlink_target_and_selected_private_root_fail_before_source_identification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    private = repository / ".git/.codex/auth.json"
    private.parent.mkdir(parents=True)
    private.write_text("private-byte-sentinel")
    (repository / "alias").symlink_to(".git/.codex/auth.json")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Private target/root must fail before source identification")

    monkeypatch.setattr(source_snapshot, "_identify_source", forbidden)
    with pytest.raises(FleetPlanInvalid, match="native authentication"):
        source_snapshot.snapshot_source_directory(
            repository,
            tmp_path / "snapshot",
            label="private symlink",
            source_path_validator=require_native_agent_source_path_privacy,
        )
    grader_root = tmp_path / "docs/certification/answers"
    grader_root.mkdir(parents=True)
    (grader_root / "answer.json").write_text("private-byte-sentinel")
    with pytest.raises(FleetPlanInvalid, match="grader-only"):
        source_snapshot.snapshot_source_directory(
            grader_root,
            tmp_path / "snapshot",
            label="private root",
            source_path_validator=require_native_agent_source_path_privacy,
        )
    # A selected private namespace cannot evade admission by aliasing a public root.
    alias = grader_root / "public-alias"
    alias.symlink_to(repository, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="grader-only"):
        source_snapshot.snapshot_source_directory(
            alias,
            tmp_path / "snapshot",
            label="private selection alias",
            source_path_validator=require_native_agent_source_path_privacy,
        )


def test_empty_native_directories_and_host_worktree_ancestor_preserve_ordinary_source_identity(
    tmp_path: Path,
) -> None:
    repository = tmp_path / ".codex/worktrees/repository"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("public_member = True\n")
    (repository / ".codex").mkdir()
    (repository / ".opencode").mkdir()
    observed = source_snapshot.snapshot_source_directory(
        repository,
        tmp_path / "snapshot",
        label="public source",
        source_path_validator=require_native_agent_source_path_privacy,
    )
    assert observed == source_snapshot_identity(repository)
