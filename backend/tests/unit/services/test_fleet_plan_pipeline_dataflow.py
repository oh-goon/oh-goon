from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.runtime_authority import authorized_command_result
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness


class _UnitProcessIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="unit-strict",
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )
    """Exercise Fleet's private workspace semantics without requiring bwrap in unit tests."""

    def configuration_identity(self) -> str:
        return "process-isolation-backend-unit-strict"

    def require_available(self, plan) -> None:
        return None

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        import oh_goon.integrations.fleet_plan_step_execution as step_execution

        assert request.attempt.resources is not None
        write_remote_resource_evidence(request.attempt.paths.root, request.attempt.resources)
        return step_execution.run_command(
            *request.argv,
            timeout=timeout,
            cwd=Path(request.cwd),
            env=dict(request.env),
            cancel_requested=cancel_requested,
        )


def _executor() -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_UnitProcessIsolation(),)
    )


PIPELINE = """version: 1
inputs:
  tag:
    type: string
    default: dev
  test_timeout_seconds:
    type: integer
    min: 1
    max: 3600
    default: 600
env:
  REGISTRY_TOKEN:
    from: host
steps:
- id: prepare
  run: sh ./ci/prepare.sh --tag ${{ inputs.tag }}
  outputs:
    IMAGE_TAG: .fleet/image-tag.txt
- run: uv run pytest --timeout ${{ inputs.test_timeout_seconds }}
- run: sh ./ci/build.sh ${{ steps.prepare.outputs.IMAGE_TAG }}-beta
- run: sh ./ci/publish.sh ${{ steps.prepare.outputs.IMAGE_TAG }}-beta
  imports:
    env: [REGISTRY_TOKEN]
"""


def test_direct_dataflow_and_explicit_step_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(PIPELINE, encoding="utf-8")
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    monkeypatch.setenv("REGISTRY_TOKEN", "registry-secret")
    monkeypatch.setenv("UNDECLARED_SECRET", "must-not-leak")

    plan = FleetPlanResolver().resolve(plan_path, inputs={"tag": "1.7.0"})
    observed: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        env = dict(kwargs["env"])
        observed.append((tuple(argv), env))
        if argv[:2] == ("sh", "./ci/prepare.sh"):
            output = Path(kwargs["cwd"]) / ".fleet" / "image-tag.txt"
            output.parent.mkdir()
            output.write_text("example-service:1.7.0\n", encoding="utf-8")
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    _executor().execute(repository, plan, cancel_requested=None)

    assert [item[0] for item in observed] == [
        ("sh", "./ci/prepare.sh", "--tag", "1.7.0"),
        ("uv", "run", "pytest", "--timeout", "600"),
        ("sh", "./ci/build.sh", "example-service:1.7.0-beta"),
        ("sh", "./ci/publish.sh", "example-service:1.7.0-beta"),
    ]
    assert all("REGISTRY_TOKEN" not in env for _, env in observed[:3])
    assert observed[3][1]["REGISTRY_TOKEN"] == "registry-secret"
    assert all("UNDECLARED_SECRET" not in env for _, env in observed)


def test_direct_values_resolve_without_environment_aliases(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(PIPELINE, encoding="utf-8")
    plan = FleetPlanResolver().resolve(plan_path)
    assert plan.steps[1].argv[-1] == "600"
    assert plan.steps[1].environment == ()
    assert plan.steps[2].argv[2] == "${{ steps.prepare.outputs.IMAGE_TAG }}-beta"
    assert plan.steps[2].environment == ()


def test_imported_goon_qualifies_embedded_local_step_output_references(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    package = goons / "demo" / "pipeline"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(PIPELINE, encoding="utf-8")
    (package / "README.md").write_text("# pipeline\n", encoding="utf-8")
    plan_path = tmp_path / "plan.yml"
    plan_path.write_text(
        """version: 1
inputs:
- tag
- test_timeout_seconds
goons:
- demo/pipeline
""",
        encoding="utf-8",
    )

    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(goons)).resolve(
        plan_path, inputs={"tag": "1.7.0", "test_timeout_seconds": "600"}
    )

    assert plan.steps[0].step_id == "demo-pipeline.prepare"
    assert plan.steps[2].argv[2] == "${{ steps.demo-pipeline.prepare.outputs.IMAGE_TAG }}-beta"
    assert plan.steps[3].argv[2] == "${{ steps.demo-pipeline.prepare.outputs.IMAGE_TAG }}-beta"


def test_shell_style_import_reference_is_rejected_before_execution(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
env:
  VALUE:
    value: hello
steps:
- id: bad
  run: "tool $VALUE"
  imports:
    env: [VALUE]
""",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="no shell expansion"):
        FleetPlanResolver().resolve(plan_path)


def test_host_env_cannot_be_interpolated_into_argv(tmp_path: Path) -> None:
    plan = tmp_path / "goon.yml"
    plan.write_text(
        """version: 1
execution:
  purpose: verification
env:
  TOKEN:
    from: host
steps:
- id: leak
  run: "tool ${{ env.TOKEN }}"
""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="cannot be interpolated into argv"):
        FleetPlanResolver().resolve(plan)


def test_unknown_goon_fields_and_bad_env_sources_fail_before_execution(tmp_path: Path) -> None:
    unknown = tmp_path / "goon.yml"
    unknown.write_text(
        """version: 1
aliases:
  A: value
steps:
- run: "true"
""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="unsupported fields: aliases"):
        FleetPlanResolver().resolve(unknown)

    with pytest.raises(FleetPlanInvalid, match="env definitions must use"):
        parse_goon_template(
            """version: 1
env:
  IMAGE_TAG:
    from: step
    step: prepare
    output: MISSING
steps:
- run: "true"
"""
        )

