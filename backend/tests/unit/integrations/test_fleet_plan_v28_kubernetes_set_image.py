from __future__ import annotations

from functools import cache
import json
from pathlib import Path

import pytest

import oh_goon.integrations._fleet_plan_host_resources as host_resources
import oh_goon.integrations.fleet_plan_kubernetes_transport as kubernetes_transport
from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.core.errors import ExternalEffectOutcomeUnknown, FleetPlanInvalid
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.fleet_plan_tool_requirements import (
    required_execution_tool_names,
    step_execution_tool,
)
from oh_goon.core.models.fleet_plan import ResolvedKubernetesSetImageStep
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_host_resources import (
    capture_host_environment,
    preflight_host_environment_feasibility,
)
from oh_goon.integrations._fleet_plan_step_dataflow import bind_step_outputs
from oh_goon.integrations._fleet_plan_step_evidence import canonical_argv
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.fleet_plan_kubernetes_execution import execute_kubernetes_set_image
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES



def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GOON_TEMPLATES)


DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
IMAGE = f"harbor.example.com/team/app@{DIGEST}"
OLD_IMAGE = f"harbor.example.com/team/app@{OTHER_DIGEST}"
TOKEN = "token-0123456789-abcd"
CA = "-----BEGIN CERTIFICATE-----\nMIICwjCCAaqgAwIBAgIBATANBgkqhkiG9w0BAQsFADAaMRgwFgYDVQQDDA9vaC1n\nb29uLXRlc3QtY2EwHhcNMjYwODE0MTg1MDMyWhcNMjcwODE1MTg1MDMyWjAaMRgw\nFgYDVQQDDA9vaC1nb29uLXRlc3QtY2EwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAw\nggEKAoIBAQDALl/pha1xZR49druzrPUqEopFYmA0NmuLB2zs2nvDEk7Ck+KA23Qx\nf0bXTQJdU4SPiXF5wzFfbrVjzHFb8fFF2zCHnknucWVDCyBe/bXQwvlX408Dbm0Q\nnNr8aQwv/D65Ub1E1ptEwj+oKomgsYXl0Sxo60I2waWupjDVSJmMwZ9SVlnY0GP/\npkdei9VfIy+oGiJqZVE03fKbjJHix28//TQ0kvmpqlShF9W50uAf7HeNCKtR8r7J\nhW6RS6SBd2V+swvFmLGg3WJbZFvlzd2gwZHm7AY/sYawyCWQ8cT51ST8X3S8mDT8\n9d4jj1czfZxkf49m9xvOuqRVypXciTr/AgMBAAGjEzARMA8GA1UdEwEB/wQFMAMB\nAf8wDQYJKoZIhvcNAQELBQADggEBACyO/q15gh0lqVviLOUD2+rcSHNtytfaOKZm\nOdD+w7CHECJceIjvfcnmkw8L97WjKFTBDHJrMX3mcRWYyZN8cU03ZGN9bToHFJug\nwKcR4K2vYYEjU/GDxxkPIHkTEV3Kwqm0fFmkZDkxg3BUcPyAweicLyeyXv58FAek\nwxKaCn7bbelI9EVHpac+0CNgswyOK47VL0Hn2UxPZNTHe9ZYcI2uaSDhUr9gOyCw\nXpaBIU6p7MUOv3YFtfJdJn4OgzdMgu+niAbAjp+hyXxFlwoT1hp3cxBTb/3eNw4k\njgvikjr7NWNBdWEzMRvC202eHuYoVEpufr8IbJAzm6B/ETrQ0Dk=\n-----END CERTIFICATE-----\n"


@cache
def _plan():
    return _resolver().resolve(
        PLAN_TEMPLATES.source("kubernetes/set-image").path,
        inputs={
            "kubernetes_api_hostname": "kube.example.com",
            "namespace": "production",
            "deployment": "api",
            "container": "api",
            "image": IMAGE,
        },
    )


def _layout(root: Path) -> AttemptPathLayout:
    layout = AttemptPathLayout.from_root(root)
    for path in (layout.home, layout.tmp, layout.cache, layout.config):
        path.mkdir(parents=True, exist_ok=True)
    return layout


def _deployment(*, image: str, resource_version: str, generation: int) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "namespace": "production",
            "resourceVersion": resource_version,
            "generation": generation,
        },
        "spec": {"template": {"spec": {"containers": [{"name": "api", "image": image}]}}},
    }



def test_kubernetes_credentials_fail_malformed_cheap_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    with pytest.raises(FleetPlanInvalid, match="invalid host credential material"):
        preflight_host_environment_feasibility(
            (plan,),
            {
                "KUBERNETES_TOKEN": "short",
                "KUBERNETES_CA_CERTIFICATE": CA,
            },
        )


def test_kubernetes_template_resolves_to_v28_exact_api_authority() -> None:
    plan = _plan()
    assert plan.contract_version == 43
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    assert step.image == IMAGE
    assert step.target.resource_url == (
        "https://kube.example.com/apis/apps/v1/namespaces/production/deployments/api"
    )
    assert step_execution_tool(step) == "curl"
    assert step.capabilities.egress.rules[0].hostname == "kube.example.com"
    assert step.capabilities.egress.rules[0].ports == (443,)
    assert step.policy.mutation == "json_patch"
    assert step.policy.test_resource_version is True
    assert step.policy.test_current_image is True
    assert step.policy.transport_retries == 0
    assert step.policy.wait_for_rollout is False
    assert step.environment == ()
    assert required_execution_tool_names(plan) == ("curl",)


def test_kubernetes_summary_exposes_authority_but_not_credential_values() -> None:
    summary = "\n".join(resolved_plan_summary(_plan()))
    assert "kubernetes_set_image:" in summary
    assert "api=kube.example.com" in summary
    assert f"image={IMAGE}" in summary
    assert "resource_version_test=true" in summary
    assert "current_image_test=true" in summary
    assert "rollout_waited=false" in summary
    assert "host:KUBERNETES_TOKEN,host:KUBERNETES_CA_CERTIFICATE" in summary
    assert "credential_values=redacted" in summary


def test_kubernetes_credentials_are_validated_before_execution(monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_TOKEN", TOKEN)
    monkeypatch.setenv("KUBERNETES_CA_CERTIFICATE", CA)
    assert capture_host_environment(_plan()) == {
        "KUBERNETES_CA_CERTIFICATE": CA,
        "KUBERNETES_TOKEN": TOKEN,
    }

    monkeypatch.setenv("KUBERNETES_TOKEN", "short")
    with pytest.raises(FleetPlanInvalid, match="invalid host credential material"):
        capture_host_environment(_plan())


def test_release_deploy_binds_exact_published_digest_reference() -> None:
    plan = _resolver().resolve(
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
    step = plan.steps[-2]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    reference = step_output_reference(step.image)
    assert reference is not None and reference[1] == "OCI_REFERENCE"
    publish = next(item for item in plan.steps if item.step_id == reference[0])
    assert publish.kind == "oci_publish"
    bound = bind_step_outputs(
        step,
        {publish.step_id: {"OCI_REFERENCE": IMAGE}},
    )
    assert isinstance(bound, ResolvedKubernetesSetImageStep)
    assert bound.image == IMAGE


def test_set_image_execution_uses_json_patch_tests_and_records_commit(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    layout = _layout(tmp_path / "attempt")
    calls: list[tuple[str, ...]] = []
    patch_payload: list[dict] = []

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        calls.append(tuple(argv))
        output = Path(argv[argv.index("--output") + 1])
        method = argv[argv.index("--request") + 1]
        if method == "GET":
            output.write_text(
                json.dumps(_deployment(image=OLD_IMAGE, resource_version="10", generation=4))
            )
        else:
            patch = Path(argv[argv.index("--data-binary") + 1][1:])
            patch_payload.extend(json.loads(patch.read_text()))
            output.write_text(
                json.dumps(_deployment(image=IMAGE, resource_version="11", generation=5))
            )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=3)

    outcome = execute_kubernetes_set_image(
        repository,
        step,
        layout,
        environment={"PATH": "/toolchain/bin", "HTTPS_PROXY": "http://ambient.invalid"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode == 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "KUBERNETES_SET_IMAGE"
    assert outcome.release_effect.outcome == "COMMITTED"
    assert outcome.release_effect.before_resource_version == "10"
    assert outcome.release_effect.after_resource_version == "11"
    assert outcome.release_effect.after_image == IMAGE
    assert [call[call.index("--request") + 1] for call in calls] == ["GET", "PATCH"]
    assert all("--disable" in call and "--retry" in call for call in calls)
    private_root = Path(calls[0][calls[0].index("--config") + 1]).parent
    assert not private_root.exists()
    assert patch_payload == [
        {"op": "test", "path": "/metadata/resourceVersion", "value": "10"},
        {"op": "test", "path": "/spec/template/spec/containers/0/name", "value": "api"},
        {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": OLD_IMAGE},
        {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": IMAGE},
    ]
    evidence = json.loads((repository / "kubernetes/deployment-image.json").read_text())
    assert evidence["mutation_committed"] is True
    assert evidence["before_resource_version"] == "10"
    assert evidence["after_resource_version"] == "11"
    assert evidence["rollout_waited"] is False
    assert (repository / "kubernetes/image-reference.txt").read_text().strip() == IMAGE
    assert canonical_argv(
        step, StepOutcome(argv=("curl", "--config", "/secret"), returncode=0)
    ) == (
        "kubernetes_set_image",
        "deployment/production/api",
        "container=api",
        f"image={IMAGE}",
    )


def test_set_image_conflict_is_known_failure_but_transport_loss_is_unknown(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)

    def run_case(root: Path, *, patch_result: CommandResult):
        repository = root / "repo"
        repository.mkdir(parents=True)
        layout = _layout(root / "attempt")
        count = 0

        def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
            nonlocal count
            count += 1
            output = Path(argv[argv.index("--output") + 1])
            if count == 1:
                output.write_text(
                    json.dumps(_deployment(image=OLD_IMAGE, resource_version="10", generation=4))
                )
                return CommandResult(
                    argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1
                )
            return (
                patch_result.model_copy(update={"argv": tuple(argv)})
                if hasattr(patch_result, "model_copy")
                else CommandResult(
                    argv=tuple(argv),
                    returncode=patch_result.returncode,
                    stdout=patch_result.stdout,
                    stderr=patch_result.stderr,
                    duration_ms=patch_result.duration_ms,
                )
            )

        return execute_kubernetes_set_image(
            repository,
            step,
            layout,
            environment={"PATH": "/toolchain/bin"},
            host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
            command_runner=runner,
            cancel_requested=None,
        )

    conflict = run_case(
        tmp_path / "conflict",
        patch_result=CommandResult(
            argv=(), returncode=22, stdout="409", stderr="conflict", duration_ms=1
        ),
    )
    assert conflict.returncode == 22

    with pytest.raises(ExternalEffectOutcomeUnknown, match="Automatic replay is blocked"):
        run_case(
            tmp_path / "unknown",
            patch_result=CommandResult(
                argv=(), returncode=28, stdout="000", stderr="timeout", duration_ms=1
            ),
        )


def test_set_image_already_desired_is_read_only_success(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    layout = _layout(tmp_path / "attempt")
    calls = 0

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        nonlocal calls
        calls += 1
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(json.dumps(_deployment(image=IMAGE, resource_version="15", generation=7)))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    outcome = execute_kubernetes_set_image(
        repository,
        step,
        layout,
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )
    assert outcome.returncode == 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "KUBERNETES_SET_IMAGE"
    assert outcome.release_effect.outcome == "ALREADY_DESIRED"
    assert (
        outcome.release_effect.before_resource_version
        == outcome.release_effect.after_resource_version
    )
    assert calls == 1
    evidence = json.loads((repository / "kubernetes/deployment-image.json").read_text())
    assert evidence["already_desired"] is True
    assert evidence["mutation_committed"] is False


def test_committed_kubernetes_effect_survives_local_evidence_failure(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_kubernetes_observation as kubernetes_observation

    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    layout = _layout(tmp_path / "attempt")
    calls = 0

    def runner(*argv: str, **_kwargs):
        nonlocal calls
        calls += 1
        output = Path(argv[argv.index("--output") + 1])
        if calls == 1:
            output.write_text(
                json.dumps(_deployment(image=OLD_IMAGE, resource_version="30", generation=9))
            )
        else:
            output.write_text(
                json.dumps(_deployment(image=IMAGE, resource_version="31", generation=10))
            )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    def fail_local_evidence(*_args, **_kwargs):
        raise OSError("simulated local evidence failure")

    monkeypatch.setattr(
        kubernetes_observation, "write_kubernetes_set_image_evidence", fail_local_evidence
    )
    outcome = execute_kubernetes_set_image(
        repository,
        step,
        layout,
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode != 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "KUBERNETES_SET_IMAGE"
    assert outcome.release_effect.outcome == "COMMITTED"
    assert outcome.release_effect.after_resource_version == "31"
    assert "mutation committed" in outcome.stderr
    assert not (repository / "kubernetes/deployment-image.json").exists()
    assert not (repository / "kubernetes/resource-version.txt").exists()
    assert not (repository / "kubernetes/generation.txt").exists()
    assert not (repository / "kubernetes/image-reference.txt").exists()


def test_committed_kubernetes_effect_reports_private_cleanup_failure(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_kubernetes_execution as kubernetes_execution

    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    layout = _layout(tmp_path / "attempt")
    calls = 0

    def runner(*argv: str, **_kwargs):
        nonlocal calls
        calls += 1
        output = Path(argv[argv.index("--output") + 1])
        deployment = _deployment(
            image=OLD_IMAGE if calls == 1 else IMAGE,
            resource_version="40" if calls == 1 else "41",
            generation=12 if calls == 1 else 13,
        )
        output.write_text(json.dumps(deployment))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    monkeypatch.setattr(
        kubernetes_execution,
        "cleanup_kubernetes_private_config",
        lambda _config: (_ for _ in ()).throw(OSError("simulated credential cleanup failure")),
    )
    outcome = execute_kubernetes_set_image(
        repository,
        step,
        layout,
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode != 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "KUBERNETES_SET_IMAGE"
    assert outcome.release_effect.outcome == "COMMITTED"
    assert outcome.release_effect.after_resource_version == "41"
    assert "credential cleanup failed" in outcome.stderr
    assert not (repository / "kubernetes/deployment-image.json").exists()
    assert not (repository / "kubernetes/resource-version.txt").exists()
    assert not (repository / "kubernetes/generation.txt").exists()
    assert not (repository / "kubernetes/image-reference.txt").exists()


def test_set_image_exact_precondition_mismatch_fails_before_patch(tmp_path: Path) -> None:
    from oh_goon.core.models.fleet_plan_kubernetes import ExactCurrentKubernetesImage

    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    step = step.model_copy(update={"precondition": ExactCurrentKubernetesImage(image=IMAGE)})
    repository = tmp_path / "repo"
    repository.mkdir()
    layout = _layout(tmp_path / "attempt")
    calls = 0

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        nonlocal calls
        calls += 1
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(
            json.dumps(_deployment(image=OLD_IMAGE, resource_version="20", generation=8))
        )
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    outcome = execute_kubernetes_set_image(
        repository,
        step,
        layout,
        environment={"PATH": "/toolchain/bin"},
        host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
        command_runner=runner,
        cancel_requested=None,
    )
    assert outcome.returncode != 0
    assert "current-image precondition failed" in outcome.stderr
    assert calls == 1
    evidence = json.loads((repository / "kubernetes/deployment-image.json").read_text())
    assert evidence["result"] == "precondition_failed"


def test_set_image_success_without_valid_commit_response_is_unknown(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    layout = _layout(tmp_path / "attempt")
    calls = 0

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        nonlocal calls
        calls += 1
        output = Path(argv[argv.index("--output") + 1])
        if calls == 1:
            output.write_text(
                json.dumps(_deployment(image=OLD_IMAGE, resource_version="30", generation=9))
            )
        else:
            output.write_text("not-json")
        return CommandResult(argv=tuple(argv), returncode=0, stdout="200", stderr="", duration_ms=1)

    with pytest.raises(ExternalEffectOutcomeUnknown, match="reported success"):
        execute_kubernetes_set_image(
            repository,
            step,
            layout,
            environment={"PATH": "/toolchain/bin"},
            host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
            command_runner=runner,
            cancel_requested=None,
        )


def test_kubernetes_model_boundaries_reject_ambiguous_authority() -> None:
    from oh_goon.core.models.fleet_plan_kubernetes import (
        AuthoringKubernetesApiCredentials,
        ExactCurrentKubernetesImage,
        KubernetesApiCredentials,
        KubernetesApiEndpoint,
        KubernetesSetImageOutput,
        is_digest_image_reference,
        validate_kubernetes_ca_certificate,
    )

    with pytest.raises(ValueError, match="canonical lowercase DNS"):
        KubernetesApiEndpoint(hostname="KUBE.example.com")
    assert (
        KubernetesApiEndpoint(hostname="kube.example.com", port=6443).authority
        == "kube.example.com:6443"
    )
    with pytest.raises(ValueError, match="must be distinct"):
        AuthoringKubernetesApiCredentials(
            token_env="TOKEN_SOURCE", ca_certificate_env="TOKEN_SOURCE"
        )
    with pytest.raises(ValueError, match="must be distinct"):
        KubernetesApiCredentials(
            token_host_env="TOKEN_SOURCE", ca_certificate_host_env="TOKEN_SOURCE"
        )
    with pytest.raises(ValueError, match="PEM certificate data"):
        validate_kubernetes_ca_certificate("not-pem")
    with pytest.raises(ValueError, match="PEM is incomplete"):
        validate_kubernetes_ca_certificate("-----BEGIN CERTIFICATE-----\nabc")
    with pytest.raises(ValueError, match="valid X.509 PEM"):
        validate_kubernetes_ca_certificate(
            "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
        )
    with pytest.raises(ValueError, match="whitespace"):
        ExactCurrentKubernetesImage(image="registry.example.com/team/app: bad")
    with pytest.raises(ValueError, match="must be distinct"):
        KubernetesSetImageOutput(metadata="same", resource_version="same")
    assert not is_digest_image_reference("harbor.example.com:99999/team/app@sha256:" + "a" * 64)


def test_set_image_read_failures_are_known_without_mutation(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)

    for name, result in (
        (
            "missing",
            CommandResult(argv=(), returncode=22, stdout="404", stderr="missing", duration_ms=1),
        ),
        (
            "network",
            CommandResult(argv=(), returncode=7, stdout="000", stderr="connect", duration_ms=1),
        ),
    ):
        repository = tmp_path / name / "repo"
        repository.mkdir(parents=True)
        layout = _layout(tmp_path / name / "attempt")

        def runner(
            *argv: str, timeout=None, cwd=None, env=None, cancel_requested=None, _result=result
        ):
            return CommandResult(
                argv=tuple(argv),
                returncode=_result.returncode,
                stdout=_result.stdout,
                stderr=_result.stderr,
                duration_ms=_result.duration_ms,
            )

        outcome = execute_kubernetes_set_image(
            repository,
            step,
            layout,
            environment={"PATH": "/toolchain/bin"},
            host_environment={"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
            command_runner=runner,
            cancel_requested=None,
        )
        assert outcome.returncode != 0
        assert not (repository / "kubernetes/image-reference.txt").exists()


def test_committed_transition_validation_rejects_inconsistent_success() -> None:
    from oh_goon.integrations.fleet_plan_kubernetes_execution import _validate_committed_transition
    from oh_goon.integrations.fleet_plan_kubernetes_release import KubernetesDeploymentObservation

    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    before = KubernetesDeploymentObservation(
        resource_version="1", generation=1, container_index=0, image=OLD_IMAGE
    )
    invalid = (
        KubernetesDeploymentObservation(
            resource_version="2", generation=2, container_index=0, image=OLD_IMAGE
        ),
        KubernetesDeploymentObservation(
            resource_version="1", generation=2, container_index=0, image=IMAGE
        ),
        KubernetesDeploymentObservation(
            resource_version="2", generation=1, container_index=0, image=IMAGE
        ),
        KubernetesDeploymentObservation(
            resource_version="2", generation=2, container_index=1, image=IMAGE
        ),
    )
    for after in invalid:
        with pytest.raises(ExternalEffectOutcomeUnknown):
            _validate_committed_transition(before, after, step)


def test_kubernetes_private_config_failure_removes_bearer_token_material(
    tmp_path: Path, monkeypatch
) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    paths = _layout(tmp_path / "attempt")
    original = kubernetes_transport.publish_text_file

    def fail_after_ca(target: Path, content: str, **kwargs) -> None:
        if target.name == "curl-auth.conf":
            raise OSError("simulated auth config failure")
        original(target, content, **kwargs)

    monkeypatch.setattr(kubernetes_transport, "publish_text_file", fail_after_ca)
    with pytest.raises(OSError, match="simulated auth config failure"):
        kubernetes_transport.prepare_kubernetes_private_config(
            paths,
            step.credentials,
            {"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
            owner="failure-clean",
        )

    assert not (paths.config / "failure-clean").exists()


def test_kubernetes_private_config_rejects_non_directory_root(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedKubernetesSetImageStep)
    paths = _layout(tmp_path / "attempt")
    root = paths.config / "occupied-root"
    root.write_text("operator-owned sentinel", encoding="utf-8")

    with pytest.raises(OSError, match="must be an owned directory"):
        kubernetes_transport.prepare_kubernetes_private_config(
            paths,
            step.credentials,
            {"KUBERNETES_TOKEN": TOKEN, "KUBERNETES_CA_CERTIFICATE": CA},
            owner="occupied-root",
        )

    assert root.read_text(encoding="utf-8") == "operator-owned sentinel"
