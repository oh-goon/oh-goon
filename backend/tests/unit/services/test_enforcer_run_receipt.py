from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from oh_goon.api import dependencies
from oh_goon.api.routes import fleet_runs
from oh_goon.cli import main
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.enforcer_receipt import EnforcerRunReceipt
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement, PlanExecutorCapacityEvidence
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedImport,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    CompletedPlanStepTeardownEvidence,
    FleetPlanResult,
    MissingPlanArtifact,
    PlanContentObjectEvidence,
    PlanEgressEvidence,
    PlanExecutionInputEvidence,
    PlanHostnameResolutionEvidence,
    PlanImportApplicabilityEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanResourceEvidence,
    PlanRetainedContentEvidence,
    PlanSnapshotEvidence,
    PlanStepAttemptEvidence,
    PlanToolEvidence,
    PresentPlanArtifact,
    PublishedPlanOutputEvidence,
    RequirementsRepositoryAdmissionEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
)
from oh_goon.core.models.fleet_plan_retry import ExitCodeNotDeclaredPlanRetryDecisionEvidence
from oh_goon.core.models.fleet_plan_tool_authority import PlanHostToolAuthorityEvidence
from oh_goon.core.models.goon_plan_source import PlanFileSource, SharedPlanSource, TeamGoonSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import (
    CompletedFleetRun,
    FleetRunDetail,
    FleetRunProgress,
    FleetRunStatus,
)
from oh_goon.core.models.run_admission import (
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import OperationAction, Verdict
from oh_goon.services.enforcer_run_receipt import enforcer_run_receipt, render_enforcer_yaml
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def _snapshot(char: str) -> PlanSnapshotEvidence:
    return PlanSnapshotEvidence(sha256=char * 64, byte_count=10, entry_count=1)


def _output() -> PlanOutputEvidence:
    return PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)


def _detail(tmp_path: Path, *, native: bool = False) -> FleetRunDetail:
    repository = tmp_path / "repo"
    repository.mkdir()
    native_agent = None
    planned: ResolvedCommandStep = ResolvedCommandStep(
        step_id="tests",
        argv=("uv", "run", "pytest"),
        timeout_seconds=60,
    )
    if native:
        native_agent = ResolvedNativeAgent(
            agent="pi",
            profile_name="selected",
            definition=NativeAgentDefinition(executable="/private/native/bin/custom-pi"),
            profile=NativeAgentProfile(),
            executable_sha256="c" * 64,
        )
        planned = ResolvedAgentStep(
            workload={"harness": "native", "native_agent": native_agent},
            step_id="tests",
            timeout_seconds=60,
            delivery_sha256="b" * 64,
            handoff_identity="handoff",
            source_step_id="tests",
        )
    plan = ResolvedFleetPlan.create(
        name="quality",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=512, max_processes=64),
        ),
        requirements=(),
        imports=(
            ResolvedImport(
                import_id="quality",
                kind="goon",
                name="python/quality",
                source_sha256="0" * 64,
                step_ids=("tests",),
                requirements=(),
            ),
        ),
        steps=(planned,),
        native_agent=native_agent,
    )
    if isinstance(planned, ResolvedAgentStep):
        target = FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_deliveries=(
                AgentStepDeliveryReference(
                    step_id=planned.step_id,
                    handoff_identity=planned.handoff_identity,
                    delivery_sha256=planned.delivery_sha256,
                    reference_id="retained-native-delivery",
                ),
            ),
        )
    else:
        target = structural_fleet_plan_target(plan)
    tool_name = "python" if native else "uv"
    tool = PlanHostToolAuthorityEvidence(name=tool_name, sha256="a" * 64, byte_count=123)
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        returncode=0,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        duration_ms=40,
        repository_state_before="repo-before",
        repository_state_after="repo-after",
        tool=PlanToolEvidence(name=tool_name, origin="host", sha256="a" * 64, byte_count=123),
        egress=PlanEgressEvidence(mode="none"),
    )
    step = CompletedPlanStepExecution(
        step_id="tests",
        kind=planned.kind,
        returncode=0,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        duration_ms=40,
        repository_state_before="repo-before",
        repository_state_after="repo-after",
        attempts=(attempt,),
        artifacts=(
            PresentPlanArtifact(
                path="reports/private-artifact.txt",
                sha256="3" * 64,
                byte_count=42,
                retained_object=PlanRetainedContentEvidence(
                    reference_id="ref-" + "4" * 32,
                    owner_repository_id="repo-" + "1" * 20,
                    kind="file",
                    sha256="3" * 64,
                    byte_count=42,
                    entry_count=1,
                ),
            ),
            MissingPlanArtifact(path="reports/secret-missing-artifact.txt"),
        ),
        outputs=(
            PublishedPlanOutputEvidence(
                name="IMAGE_TAG",
                sha256="5" * 64,
                byte_count=17,
            ),
        ),
        teardown=CompletedPlanStepTeardownEvidence(attempt_roots_observed=1),
    )
    inputs = PlanExecutionInputEvidence(
        source_authority_id="repository-source-authority-" + "0" * 64,
        repository_id="repo-" + "1" * 20,
        base_workspace=_snapshot("b"),
        repository_admission=RequirementsRepositoryAdmissionEvidence(
            requirements_id="requirements-1",
            requirement_count=1,
        ),
        import_applicability=(
            PlanImportApplicabilityEvidence(import_id="quality", status="APPLICABLE", reason=""),
        ),
        runtime_root=_snapshot("e"),
        isolation_backend=PlanIsolationBackendEvidence(
            backend_id="bubblewrap",
            configuration_id="cfg-1",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
        host_environment_names=("REGISTRY_USER",),
        host_tools=(tool,),
        managed_inputs=(_snapshot("f"),),
        content_inputs=(
            PlanContentObjectEvidence(kind="tree", sha256="c" * 64, byte_count=20, entry_count=2),
        ),
        hostname_resolutions=(
            PlanHostnameResolutionEvidence(
                hostname="registry.example.com",
                addresses=("192.0.2.10", "192.0.2.20"),
            ),
        ),
    )
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(step,), execution_inputs=inputs)
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=result),
        verdict=Verdict.READY,
        reason_code=ReasonCode.READY,
    )
    now = utc_now()
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="run-enforcer",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(report.repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    backend = inputs.isolation_backend
    capacity = PlanExecutorCapacityEvidence(
        fleet_run_id=run.fleet_run_id,
        pool_id="pool",
        placement_policy="priority_rendezvous_fifo_v2",
        placement_rank=0,
        executor_priority=0,
        executor_id="local",
        executor_generation=1,
        source="local_verified",
        backend_configuration_id=backend.configuration_id,
        capacity_cpu_millis=8000,
        capacity_memory_bytes=8 * 1024 * 1024 * 1024,
        capacity_max_processes=512,
        requested_parallelism=1,
        admitted_parallelism=1,
        per_attempt_cpu_millis=1000,
        per_attempt_memory_bytes=512 * 1024 * 1024,
        per_attempt_max_processes=64,
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=StrictFleetExecutionAdmission(
            placement=FleetExecutorPlacement(
                isolation_backend=backend,
                executor_capacity=capacity,
                executor_trust_epoch=0,
            ),
        ),
        operations=(report,),
    )


def test_historical_native_receipt_keeps_selected_label_and_launcher_evidence_path_free(
    tmp_path: Path,
) -> None:
    detail = FleetRunDetail.model_validate_json(
        _detail(tmp_path, native=True).model_dump_json(exclude_computed_fields=True)
    )
    receipt = enforcer_run_receipt(detail)
    rendered = render_enforcer_yaml(receipt)

    assert receipt.plan.steps[0].required_tool == "custom-pi"
    tool = receipt.repositories[0].steps[0].attempts[0].tool
    assert tool is not None and tool.name == "python" and tool.sha256 == "a" * 64
    assert "/private/native" not in rendered
    assert "required_tool: custom-pi" in rendered


def test_enforcer_yaml_is_deterministic_and_excludes_sensitive_runtime_values(
    tmp_path: Path,
) -> None:
    receipt = enforcer_run_receipt(_detail(tmp_path))
    rendered = render_enforcer_yaml(receipt)

    assert rendered == render_enforcer_yaml(receipt)
    assert "receipt_format: oh-goon/enforcer-run/v19" in rendered
    assert yaml.safe_load(rendered)["execution_reference"]["source"] == {
        "kind": "plan_file",
        "sha256": "0" * 64,
    }
    assert "required_tool: uv" in rendered
    assert "required_tools:" not in rendered
    assert "REGISTRY_USER" in rendered
    assert "uv run pytest" not in rendered
    assert str(tmp_path) not in rendered
    assert "argv:" not in rendered
    assert "addresses:" not in rendered
    assert "192.0.2.10" not in rendered
    assert "192.0.2.20" not in rendered
    payload = yaml.safe_load(rendered)
    execution_input = payload["repositories"][0]["execution_input"]
    assert execution_input["hostname_resolutions"][0]["hostname"] == "registry.example.com"
    assert len(execution_input["hostname_resolutions"][0]["sha256"]) == 64
    assert execution_input["managed_inputs"][0]["sha256"] == "f" * 64
    assert execution_input["content_inputs"][0] == {
        "kind": "tree",
        "sha256": "c" * 64,
        "byte_count": 20,
        "entry_count": 2,
    }
    for removed in (
        "execution_input_id",
        "hostname_resolution_fingerprints",
        "managed_input_digests",
        "content_input_digests",
    ):
        assert removed not in payload["repositories"][0]
    repository = payload["repositories"][0]
    assert repository["operation"]["operation_id"].startswith("op-")
    assert repository["operation"]["outcome"]["state"] == "successful"
    for removed in ("operation_id", "operation_state", "verdict", "returncode", "reason_code"):
        assert removed not in repository


def test_enforcer_receipt_v19_records_structural_execution_facts_requirements_and_imports(
    tmp_path: Path,
) -> None:
    receipt = enforcer_run_receipt(_detail(tmp_path))
    rendered = render_enforcer_yaml(receipt)

    assert "kind: requirements" in rendered
    assert "requirements_id: requirements-1" in rendered
    assert "requirement_count: 1" in rendered
    payload = yaml.safe_load(rendered)
    assert payload["plan"]["execution"] == {
        "purpose": "cicd",
        "trust_repository_code": True,
        "resources": {"cpu_millis": 1000, "memory_mib": 512, "max_processes": 64},
        "repositories": {"mode": "serial"},
    }
    assert payload["fleet_execution"]["kind"] == "strict"
    assert (
        payload["fleet_execution"]["placement"]["isolation_backend"]["backend_id"] == "bubblewrap"
    )
    capacity = payload["fleet_execution"]["placement"]["executor_capacity"]
    assert capacity["executor_id"] == "local"
    assert capacity["fleet_run_id"] == "run-enforcer"
    assert capacity["admitted_parallelism"] == 1
    assert "imports:" in rendered
    assert "import_id: quality" in rendered
    assert "name: python/quality" in rendered
    assert "import_applicability:" in rendered
    assert "status: APPLICABLE" in rendered
    assert "pyproject_sha256" not in rendered
    assert "pre_commit_sha256" not in rendered


def _with_source(
    detail: FleetRunDetail, source: object, *, repositories: int = 1
) -> FleetRunDetail:
    target = detail.run.target.model_copy(update={"source": source})
    targets = detail.run.targets
    if repositories == 2:
        targets = targets + (
            targets[0].model_copy(update={"repository_id": "repo-" + "2" * 20, "name": "repo-two"}),
        )
    run = detail.run.model_copy(update={"target": target, "targets": targets})
    return detail.model_copy(update={"run": run})


def test_team_goon_receipt_uses_structural_source_and_one_repository_scope(tmp_path: Path) -> None:
    detail = _with_source(
        _detail(tmp_path),
        TeamGoonSource(sha256="1" * 64),
    )

    receipt = enforcer_run_receipt(detail)

    assert receipt.execution_reference.source == TeamGoonSource(sha256="1" * 64)
    assert len(receipt.repositories) == 1
    rendered = render_enforcer_yaml(receipt)
    assert yaml.safe_load(rendered)["execution_reference"]["source"] == {
        "kind": "team_goon",
        "sha256": "1" * 64,
    }
    assert "plan_source_kind" not in rendered
    assert "plan_source_id" not in rendered
    assert "plan_source_sha256" not in rendered


def test_shared_goon_receipt_uses_one_source_across_selected_repositories(tmp_path: Path) -> None:
    detail = _with_source(
        _detail(tmp_path),
        SharedPlanSource(plan_key="shared-quality", sha256="2" * 64),
        repositories=2,
    )

    receipt = enforcer_run_receipt(detail)

    assert receipt.execution_reference.source == SharedPlanSource(
        plan_key="shared-quality",
        sha256="2" * 64,
    )
    assert tuple(repository.repository_id for repository in receipt.repositories) == (
        detail.run.targets[0].repository_id,
        "repo-" + "2" * 20,
    )
    rendered = render_enforcer_yaml(receipt)
    assert yaml.safe_load(rendered)["execution_reference"]["source"] == {
        "kind": "shared_plan",
        "plan_key": "shared-quality",
        "sha256": "2" * 64,
    }
    assert rendered.count("kind: shared_plan") == 1
    assert "plan_source_kind" not in rendered
    assert "plan_source_id" not in rendered
    assert "plan_source_sha256" not in rendered


def test_team_goon_receipt_rejects_multiple_repository_facts(tmp_path: Path) -> None:
    detail = _with_source(
        _detail(tmp_path),
        SharedPlanSource(plan_key="shared-quality", sha256="2" * 64),
        repositories=2,
    )
    payload = enforcer_run_receipt(detail).model_dump(mode="json", exclude_computed_fields=True)
    payload["execution_reference"]["source"] = TeamGoonSource(sha256="1" * 64).model_dump(
        mode="json"
    )

    with pytest.raises(ValidationError, match="Team Goon source requires exactly one repository"):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v19_rejects_flat_repository_execution_inputs(tmp_path: Path) -> None:
    receipt = enforcer_run_receipt(_detail(tmp_path))
    payload = receipt.model_dump(mode="json", exclude_computed_fields=True)
    repository = payload["repositories"][0]
    execution_input = repository.pop("execution_input")
    repository["execution_input_id"] = execution_input["input_id"]
    repository["base_workspace"] = execution_input["base_workspace"]
    repository["runtime_root"] = execution_input["runtime_root"]
    repository["host_environment_names"] = execution_input["host_environment_names"]
    repository["hostname_resolution_fingerprints"] = ["legacy"]
    repository["host_toolchain_id"] = execution_input["host_toolchain_id"]
    repository["managed_input_digests"] = ["f" * 64]
    repository["content_input_digests"] = ["c" * 64]

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v17_rejects_flat_repository_operation_outcome(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    repository = payload["repositories"][0]
    operation = repository.pop("operation")
    outcome = operation["outcome"]
    repository.update(
        operation_id=operation["operation_id"],
        operation_state=outcome["state"],
        verdict=outcome["verdict"],
        returncode=outcome["returncode"],
        reason_code=outcome["reason_code"],
    )

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v17_rejects_v16_receipt_format(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    payload["receipt_format"] = "oh-goon/enforcer-run/v16"

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def _with_structural_attempt_evidence(
    detail: FleetRunDetail,
) -> tuple[FleetRunDetail, PlanStepAttemptEvidence]:
    report = detail.operations[0]
    result = report.evidence.plan
    step = result.steps[0]
    attempt = step.attempts[0].model_copy(
        update={
            "cache_input": _snapshot("1"),
            "cache_output": _snapshot("2"),
            "resources": PlanResourceEvidence(
                cpu_millis=1000,
                memory_bytes=512 * 1024 * 1024,
                max_processes=64,
            ),
            "retry": ExitCodeNotDeclaredPlanRetryDecisionEvidence(exit_code=0),
        }
    )
    updated_step = step.model_copy(update={"attempts": (attempt,)})
    updated_result = result.model_copy(update={"steps": (updated_step,)})
    updated_evidence = report.evidence.model_copy(update={"plan": updated_result})
    updated_report = report.model_copy(update={"evidence": updated_evidence})
    return detail.model_copy(update={"operations": (updated_report,)}), attempt


def test_enforcer_receipt_v17_groups_privacy_safe_execution_inputs(tmp_path: Path) -> None:
    detail = _detail(tmp_path)
    receipt = enforcer_run_receipt(detail)
    repository = receipt.repositories[0]
    execution_input = repository.execution_input
    assert execution_input is not None
    inputs = detail.operations[0].evidence.plan.execution_inputs
    assert inputs is not None

    assert execution_input.input_id == inputs.input_id
    assert execution_input.base_workspace == inputs.base_workspace
    assert execution_input.runtime_root == inputs.runtime_root
    assert execution_input.host_environment_names == inputs.host_environment_names
    assert execution_input.managed_inputs == inputs.managed_inputs
    assert execution_input.content_inputs == inputs.content_inputs
    assert execution_input.import_applicability == inputs.import_applicability
    assert execution_input.host_toolchain_id == inputs.host_toolchain_id
    assert execution_input.host_tools[0].authority_id == inputs.host_tools[0].authority_id
    assert execution_input.hostname_resolutions[0].hostname == "registry.example.com"
    assert execution_input.hostname_resolutions[0].sha256


def test_enforcer_receipt_v17_reuses_exact_plan_and_fleet_execution_facts(tmp_path: Path) -> None:
    detail = _detail(tmp_path)
    receipt = enforcer_run_receipt(detail)

    assert receipt.plan.execution == detail.run.target.plan.execution
    assert receipt.fleet_execution.placement == detail.execution.placement
    assert receipt.fleet_execution.readmitted_from_run_id == detail.execution.readmitted_from_run_id
    assert receipt.fleet_execution.rerun_from_run_id == detail.execution.rerun_from_run_id


def test_enforcer_receipt_v17_rejects_placement_for_another_run(tmp_path: Path) -> None:
    receipt = enforcer_run_receipt(_detail(tmp_path))
    payload = receipt.model_dump(mode="json", exclude_computed_fields=True)
    payload["fleet_run_id"] = "other-run"

    with pytest.raises(ValidationError, match="placement must belong to the same Fleet run"):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v17_groups_privacy_safe_attempt_execution(tmp_path: Path) -> None:
    detail, canonical_attempt = _with_structural_attempt_evidence(_detail(tmp_path))
    receipt = enforcer_run_receipt(detail)
    attempt = receipt.repositories[0].steps[0].attempts[0]

    assert attempt.cache_input == canonical_attempt.cache_input
    assert attempt.cache_output == canonical_attempt.cache_output
    assert attempt.retry == canonical_attempt.retry
    assert attempt.resource_policy_id == canonical_attempt.resources.policy_id
    assert attempt.tool is not None
    assert attempt.tool.name == canonical_attempt.tool.name
    assert attempt.egress.mode == canonical_attempt.egress.mode
    assert attempt.egress.policy_id == canonical_attempt.egress.policy_id

    payload = yaml.safe_load(render_enforcer_yaml(receipt))
    rendered_attempt = payload["repositories"][0]["steps"][0]["attempts"][0]
    assert rendered_attempt["cache_input"]["sha256"] == "1" * 64
    for removed in (
        "cache_input_sha256",
        "cache_output_sha256",
        "egress_mode",
        "egress_policy_id",
        "retry_disposition",
        "retry_reason",
        "retry_delay_seconds",
        "next_attempt",
    ):
        assert removed not in rendered_attempt


def test_enforcer_receipt_v17_rejects_flat_attempt_execution_fields(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    attempt = payload["repositories"][0]["steps"][0]["attempts"][0]
    egress = attempt.pop("egress")
    attempt["egress_mode"] = egress["mode"]
    attempt["egress_policy_id"] = egress["policy_id"]

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v17_rejects_raw_egress_rules(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    egress = payload["repositories"][0]["steps"][0]["attempts"][0]["egress"]
    egress["rules"] = [{"cidr": "10.0.0.0/8", "protocol": "tcp", "ports": [443]}]

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v17_reuses_safe_step_evidence_and_redacts_artifact_paths(
    tmp_path: Path,
) -> None:
    detail = _detail(tmp_path)
    canonical_step = detail.operations[0].evidence.plan.steps[0]
    receipt = enforcer_run_receipt(detail)
    step = receipt.repositories[0].steps[0]

    assert step.teardown == canonical_step.teardown
    assert step.outputs == canonical_step.outputs
    assert step.artifact_digests == ("3" * 64,)

    payload = yaml.safe_load(render_enforcer_yaml(receipt))
    rendered_step = payload["repositories"][0]["steps"][0]
    assert rendered_step["teardown"] == {
        "status": "COMPLETED",
        "attempt_roots_observed": 1,
        "step_attempts_root_absent": True,
    }
    assert rendered_step["outputs"] == [{"name": "IMAGE_TAG", "sha256": "5" * 64, "byte_count": 17}]
    assert rendered_step["artifact_digests"] == ["3" * 64]
    rendered = render_enforcer_yaml(receipt)
    assert "private-artifact.txt" not in rendered
    assert "secret-missing-artifact.txt" not in rendered
    for removed in ("teardown_status", "output_digests"):
        assert removed not in rendered_step


def test_enforcer_receipt_v17_rejects_flat_step_result_fields(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    step = payload["repositories"][0]["steps"][0]
    teardown = step.pop("teardown")
    outputs = step.pop("outputs")
    step["teardown_status"] = teardown["status"]
    step["output_digests"] = [item["sha256"] for item in outputs]

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_historical_run_exposes_downloadable_enforcer_yaml(tmp_path: Path) -> None:
    detail = _detail(tmp_path)

    class Manager:
        def get(self, fleet_run_id: str) -> FleetRunDetail:
            assert fleet_run_id == detail.run.fleet_run_id
            return detail

    app = FastAPI()
    app.include_router(fleet_runs.router, prefix="/api")
    app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: Manager()
    response = TestClient(app).get(f"/api/fleet/runs/{detail.run.fleet_run_id}/enforcer.yml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert "enforcer.yml" in response.headers["content-disposition"]
    assert "host_toolchain_id:" in response.text


def test_enforcer_compare_uses_stable_yaml_receipts(tmp_path: Path, capsys) -> None:
    receipt = enforcer_run_receipt(_detail(tmp_path))
    before = tmp_path / "before-enforcer.yml"
    after = tmp_path / "after-enforcer.yml"
    before.write_text(render_enforcer_yaml(receipt), encoding="utf-8")
    after.write_text(
        render_enforcer_yaml(
            receipt.model_copy(
                update={
                    "lifecycle": receipt.lifecycle.model_copy(
                        update={"status": FleetRunStatus.FAILED}
                    )
                }
            )
        ),
        encoding="utf-8",
    )

    result = main(["enforcer", "compare", str(before), str(after)])

    assert result == 1
    output = capsys.readouterr().out
    assert "-  status: COMPLETED" in output
    assert "+  status: FAILED" in output
