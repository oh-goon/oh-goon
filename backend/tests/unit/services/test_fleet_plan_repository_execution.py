from pathlib import Path

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.capability_registry import capability_for
from oh_goon.core.models.fleet_plan import (
    ExecutionPurpose,
    ParallelRepositoryExecution,
    SerialRepositoryExecution,
)
from oh_goon.core.vocabulary import OperationAction
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


def test_plan_execution_purpose_is_explicit_and_parallel_safe_purposes_opt_in(
    tmp_path: Path,
) -> None:
    serial_path = tmp_path / "serial" / "goon.yml"
    serial_path.parent.mkdir()
    serial_path.write_text(
        """version: 1
execution:
  purpose: maintenance
steps:
  - id: test
    run: "uv run pytest"
""",
        encoding="utf-8",
    )
    parallel_path = tmp_path / "parallel" / "goon.yml"
    parallel_path.parent.mkdir()
    parallel_path.write_text(
        """version: 1
execution:
  purpose: verification
  repositories:
    mode: parallel
    max_parallel: 6
steps:
  - id: test
    run: "uv run pytest"
""",
        encoding="utf-8",
    )
    cicd_path = tmp_path / "cicd" / "goon.yml"
    cicd_path.parent.mkdir()
    cicd_path.write_text(
        """version: 1
execution:
  resources:
    cpu_millis: 2000
    memory_mib: 4096
    max_processes: 256
  repositories:
    mode: parallel
    max_parallel: 2
steps:
  - id: build
    run: "uv build"
""",
        encoding="utf-8",
    )
    serial_plan = FleetPlanResolver().resolve(serial_path)
    parallel_plan = FleetPlanResolver().resolve(parallel_path)
    serial_target = structural_fleet_plan_target(serial_plan)
    parallel_target = structural_fleet_plan_target(parallel_plan)
    definition = capability_for(OperationAction.EXECUTE_PLAN, serial_target)

    assert serial_plan.execution.purpose is ExecutionPurpose.MAINTENANCE
    assert isinstance(serial_plan.execution.repositories, SerialRepositoryExecution)
    assert parallel_plan.execution.purpose is ExecutionPurpose.VERIFICATION
    assert isinstance(parallel_plan.execution.repositories, ParallelRepositoryExecution)
    assert parallel_plan.execution.repositories.max_parallel == 6
    assert definition.parallel_safe is False
    assert (
        ResolvedCapabilityIntent(definition=definition, target=serial_target).parallel_safe is False
    )
    parallel_intent = ResolvedCapabilityIntent(definition=definition, target=parallel_target)
    assert parallel_intent.parallel_safe is True
    assert parallel_intent.max_parallel == 6
    assert serial_plan.plan_id != parallel_plan.plan_id
    cicd_plan = FleetPlanResolver().resolve(cicd_path)
    assert isinstance(cicd_plan.execution.repositories, ParallelRepositoryExecution)
    assert cicd_plan.execution.repositories.max_parallel == 2
