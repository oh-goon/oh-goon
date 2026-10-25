from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.fleet_plan_tool_requirements import required_execution_tool_names
from oh_goon.core.models.fleet_plan import (
    ResolvedFleetPlan,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
)
from oh_goon.core.models.fleet_plan_release_evidence import KubernetesRolloutVerificationEvidence
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_step_dataflow import bind_step_outputs
from oh_goon.integrations._fleet_plan_step_evidence import canonical_argv
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.fleet_plan_kubernetes_rollout_execution import (
    execute_kubernetes_rollout_verify,
)
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GOON_TEMPLATES)


DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
IMAGE = f"harbor.example.com/team/app@{DIGEST}"
OTHER_IMAGE = f"harbor.example.com/team/app@{OTHER_DIGEST}"
TOKEN = "token-0123456789-abcd"
CA = """-----BEGIN CERTIFICATE-----
MIICwjCCAaqgAwIBAgIBATANBgkqhkiG9w0BAQsFADAaMRgwFgYDVQQDDA9vaC1n
b29uLXRlc3QtY2EwHhcNMjYwODE0MTg1MDMyWhcNMjcwODE1MTg1MDMyWjAaMRgw
FgYDVQQDDA9vaC1nb29uLXRlc3QtY2EwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAw
ggEKAoIBAQDALl/pha1xZR49druzrPUqEopFYmA0NmuLB2zs2nvDEk7Ck+KA23Qx
f0bXTQJdU4SPiXF5wzFfbrVjzHFb8fFF2zCHnknucWVDCyBe/bXQwvlX408Dbm0Q
nNr8aQwv/D65Ub1E1ptEwj+oKomgsYXl0Sxo60I2waWupjDVSJmMwZ9SVlnY0GP/
pkdei9VfIy+oGiJqZVE03fKbjJHix28//TQ0kvmpqlShF9W50uAf7HeNCKtR8r7J
hW6RS6SBd2V+swvFmLGg3WJbZFvlzd2gwZHm7AY/sYawyCWQ8cT51ST8X3S8mDT8
9d4jj1czfZxkf49m9xvOuqRVypXciTr/AgMBAAGjEzARMA8GA1UdEwEB/wQFMAMB
Af8wDQYJKoZIhvcNAQELBQADggEBACyO/q15gh0lqVviLOUD2+rcSHNtytfaOKZm
OdD+w7CHECJceIjvfcnmkw8L97WjKFTBDHJrMX3mcRWYyZN8cU03ZGN9bToHFJug
wKcR4K2vYYEjU/GDxxkPIHkTEV3Kwqm0fFmkZDkxg3BUcPyAweicLyeyXv58FAek
wxKaCn7bbelI9EVHpac+0CNgswyOK47VL0Hn2UxPZNTHe9ZYcI2uaSDhUr9gOyCw
XpaBIU6p7MUOv3YFtfJdJn4OgzdMgu+niAbAjp+hyXxFlwoT1hp3cxBTb/3eNw4k
jgvikjr7NWNBdWEzMRvC202eHuYoVEpufr8IbJAzm6B/ETrQ0Dk=
-----END CERTIFICATE-----
"""


@cache
def _plan() -> ResolvedFleetPlan:
    return _resolver().resolve(
        PLAN_TEMPLATES.source("oci/release-deploy").path,
        inputs={
            "base_registry_hostname": "registry.example.com",
            "publish_registry_hostname": "harbor.example.com",
            "publish_repository": "team/app",
            "kubernetes_api_hostname": "kube.example.com",
            "namespace": "production",
            "deployment": "api",
            "container": "api",
        },
    )


def _bound_step(
    *, generation: str = "5", image: str = IMAGE
) -> ResolvedKubernetesRolloutVerifyStep:
    step = _plan().steps[-1]
    assert isinstance(step, ResolvedKubernetesRolloutVerifyStep)
    generation_ref = step_output_reference(step.expected_generation)
    image_ref = step_output_reference(step.expected_image)
    assert generation_ref is not None and image_ref is not None
    assert generation_ref[0] == image_ref[0]
    bound = bind_step_outputs(
        step,
        {
            generation_ref[0]: {
                "KUBERNETES_GENERATION": generation,
                "KUBERNETES_IMAGE": image,
            }
        },
    )
    assert isinstance(bound, ResolvedKubernetesRolloutVerifyStep)
    return bound


def _layout(root: Path) -> AttemptPathLayout:
    layout = AttemptPathLayout.from_root(root)
    for path in (layout.home, layout.tmp, layout.cache, layout.config):
        path.mkdir(parents=True, exist_ok=True)
    return layout


def _deployment(
    *,
    generation: int = 5,
    image: str = IMAGE,
    observed_generation: int | None = 5,
    desired: int = 2,
    replicas: int = 2,
    updated: int = 2,
    available: int = 2,
    unavailable: int = 0,
    progressing_status: str = "True",
    progressing_reason: str = "NewReplicaSetAvailable",
) -> dict:
    status = {
        "replicas": replicas,
        "updatedReplicas": updated,
        "availableReplicas": available,
        "unavailableReplicas": unavailable,
        "conditions": [
            {
                "type": "Progressing",
                "status": progressing_status,
                "reason": progressing_reason,
            }
        ],
    }
    if observed_generation is not None:
        status["observedGeneration"] = observed_generation
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "namespace": "production",
            "resourceVersion": "11",
            "generation": generation,
        },
        "spec": {
            "replicas": desired,
            "template": {"spec": {"containers": [{"name": "api", "image": image}]}},
        },
        "status": status,
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_release_template_resolves_v29_with_separate_explicit_rollout_gate() -> None:
    plan = _plan()
    assert plan.contract_version == 44
    commit, verify = plan.steps[-2:]
    assert isinstance(commit, ResolvedKubernetesSetImageStep)
    assert isinstance(verify, ResolvedKubernetesRolloutVerifyStep)
    assert step_output_reference(verify.expected_generation) == (
        commit.step_id,
        "KUBERNETES_GENERATION",
    )
    assert step_output_reference(verify.expected_image) == (
        commit.step_id,
        "KUBERNETES_IMAGE",
    )
    assert verify.target == commit.target
    assert verify.credentials == commit.credentials
    assert verify.policy.transport_retries == 0
    assert verify.policy.poll_interval_seconds == 5
    assert verify.timeout_seconds == 300
    assert verify.retry.max_attempts == 1
    assert required_execution_tool_names(plan) == ("buildah", "crun", "curl", "skopeo", "uv")
    summary = "\n".join(resolved_plan_summary(plan))
    assert "kubernetes_set_image:" in summary
    assert "rollout_waited=false" in summary
    assert "kubernetes_rollout_verify:" in summary
    assert "mutation=none" in summary
    assert "poll_interval=5s" in summary


def test_rollout_step_binds_exact_generation_and_image_outputs() -> None:
    step = _bound_step()
    assert step.expected_generation == "5"
    assert step.expected_image == IMAGE
    assert canonical_argv(step, StepOutcome(argv=("curl", "secret"), returncode=0)) == (
        "kubernetes_rollout_verify",
        "deployment/production/api",
        "container=api",
        "generation=5",
        f"image={IMAGE}",
    )


def test_rollout_verification_polls_read_only_until_exact_generation_is_ready(
    tmp_path: Path,
) -> None:
    step = _bound_step()
    clock = _Clock()
    calls: list[tuple[str, ...]] = []
    environments: list[dict[str, str]] = []

    responses = [
        _deployment(replicas=2, updated=1, available=1, unavailable=1),
        _deployment(),
    ]

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        calls.append(tuple(argv))
        environments.append(dict(env or {}))
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(json.dumps(responses[len(calls) - 1]))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=2)

    outcome = execute_kubernetes_rollout_verify(
        step,
        _layout(tmp_path / "attempt"),
        environment={"PATH": "/toolchain/bin", "HTTPS_PROXY": "http://ambient.invalid"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert outcome.returncode == 0
    assert len(calls) == 2
    assert all(call[call.index("--request") + 1] == "GET" for call in calls)
    assert all("--data-binary" not in call for call in calls)
    assert all("HTTPS_PROXY" not in env for env in environments)
    evidence = outcome.release_verification
    assert evidence is not None
    assert evidence.kind == "KUBERNETES_ROLLOUT_VERIFY"
    assert evidence.outcome == "READY"
    assert evidence.expected_generation == 5
    assert evidence.final_generation == 5
    assert evidence.final_image == IMAGE
    assert evidence.observations == 2
    assert evidence.updated_replicas == 2
    assert evidence.available_replicas == 2
    assert evidence.unavailable_replicas == 0
    assert evidence.transport_retries == 0


def test_ready_rollout_reports_private_cleanup_failure_without_losing_observation(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_kubernetes_rollout_execution as rollout_execution

    step = _bound_step()

    def runner(*argv: str, **_kwargs):
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(json.dumps(_deployment()))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    monkeypatch.setattr(
        rollout_execution,
        "cleanup_kubernetes_private_config",
        lambda _config: (_ for _ in ()).throw(OSError("simulated credential cleanup failure")),
    )
    outcome = execute_kubernetes_rollout_verify(
        step,
        _layout(tmp_path / "attempt"),
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode != 0
    assert outcome.release_verification is not None
    assert outcome.release_verification.outcome == "READY"
    assert "credential cleanup failed" in outcome.stderr


def test_rollout_verification_fails_closed_if_generation_is_superseded(tmp_path: Path) -> None:
    step = _bound_step()

    def runner(*argv: str, **_kwargs):
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(
            json.dumps(_deployment(generation=6, image=OTHER_IMAGE, observed_generation=6))
        )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    outcome = execute_kubernetes_rollout_verify(
        step,
        _layout(tmp_path / "attempt"),
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )
    assert outcome.returncode != 0
    assert outcome.release_verification is not None
    assert outcome.release_verification.outcome == "SUPERSEDED"
    assert outcome.release_verification.final_generation == 6


def test_rollout_verification_fails_fast_on_progress_deadline(tmp_path: Path) -> None:
    step = _bound_step()

    def runner(*argv: str, **_kwargs):
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(
            json.dumps(
                _deployment(
                    replicas=2,
                    updated=1,
                    available=1,
                    unavailable=1,
                    progressing_status="False",
                    progressing_reason="ProgressDeadlineExceeded",
                )
            )
        )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    outcome = execute_kubernetes_rollout_verify(
        step,
        _layout(tmp_path / "attempt"),
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )
    assert outcome.returncode != 0
    assert outcome.release_verification is not None
    assert outcome.release_verification.outcome == "PROGRESS_DEADLINE_EXCEEDED"


def test_rollout_timeout_records_last_authoritative_observation(tmp_path: Path) -> None:
    base = _bound_step()
    step = base.model_copy(
        update={
            "timeout_seconds": 1.0,
            "policy": base.policy.model_copy(update={"poll_interval_seconds": 0.5}),
        }
    )
    clock = _Clock()
    calls = 0

    def runner(*argv: str, **_kwargs):
        nonlocal calls
        calls += 1
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(
            json.dumps(_deployment(replicas=2, updated=1, available=1, unavailable=1))
        )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    outcome = execute_kubernetes_rollout_verify(
        step,
        _layout(tmp_path / "attempt"),
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
        clock=clock,
        sleeper=clock.sleep,
    )
    assert calls == 2
    assert outcome.returncode != 0
    assert outcome.release_verification is not None
    assert outcome.release_verification.outcome == "TIMEOUT"
    assert outcome.release_verification.observations == 2


def test_resolved_plan_rejects_rollout_target_different_from_release_step() -> None:
    plan = _plan()
    payload = plan.model_dump(mode="python")
    verify = payload["steps"][-1]
    verify["target"]["namespace"] = "other"
    with pytest.raises(ValueError, match="target/credentials must exactly match"):
        ResolvedFleetPlan.model_validate(payload)


def test_completed_rollout_evidence_requires_ready_verification() -> None:
    empty_output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        argv=("kubernetes_rollout_verify",),
        returncode=0,
        stdout_evidence=empty_output,
        stderr_evidence=empty_output,
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none", rules=()),
    )
    step = CompletedPlanStepExecution(
        step_id="verify",
        kind="kubernetes_rollout_verify",
        argv=("kubernetes_rollout_verify",),
        stdout_evidence=empty_output,
        stderr_evidence=empty_output,
        repository_state_before="before",
        repository_state_after="after",
        attempts=(attempt,),
    )
    with pytest.raises(ValueError, match="requires final READY"):
        FleetPlanResult(plan_id="plan", steps=(step,))


def test_rollout_verification_survives_canonical_result_json_round_trip() -> None:
    empty_output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    verification = KubernetesRolloutVerificationEvidence(
        outcome="READY",
        api_authority="kube.example.com",
        namespace="production",
        deployment="api",
        container="api",
        expected_generation=5,
        expected_image=IMAGE,
        observations=2,
        final_resource_version="11",
        final_generation=5,
        final_image=IMAGE,
        observed_generation=5,
        desired_replicas=2,
        replicas=2,
        updated_replicas=2,
        available_replicas=2,
        unavailable_replicas=0,
        progressing_status="True",
        progressing_reason="NewReplicaSetAvailable",
        timeout_seconds=300,
        poll_interval_seconds=5,
        request_timeout_seconds=10,
    )
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        argv=("kubernetes_rollout_verify",),
        returncode=0,
        stdout_evidence=empty_output,
        stderr_evidence=empty_output,
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none", rules=()),
        release_verification=verification,
    )
    result = FleetPlanResult(
        plan_id="plan",
        steps=(
            CompletedPlanStepExecution(
                step_id="verify",
                kind="kubernetes_rollout_verify",
                argv=("kubernetes_rollout_verify",),
                stdout_evidence=empty_output,
                stderr_evidence=empty_output,
                repository_state_before="before",
                repository_state_after="after",
                attempts=(attempt,),
            ),
        ),
    )

    restored = FleetPlanResult.model_validate_json(
        result.model_dump_json(exclude_computed_fields=True)
    )
    receipt = restored.steps[0].attempts[0].release_verification
    assert receipt is not None
    assert receipt.outcome == "READY"
    assert receipt.expected_generation == 5
    assert receipt.final_image == IMAGE
    assert receipt.verification_id == verification.verification_id
