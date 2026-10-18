from pathlib import Path
from typing import cast
from unittest.mock import Mock

from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _extension_store(root: Path) -> FleetExtensionStore:
    return FleetExtensionStore(root / "extensions", path_authority=TEST_PATH_AUTHORITY)


def _tools(root: Path) -> FleetToolStore:
    return FleetToolStore(root / "tools", path_authority=TEST_PATH_AUTHORITY)


def test_plan_run_carries_expected_repository_instances_into_fleet_admission(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1\nexecution:\n  purpose: verification\nsteps:\n  - id: tests\n    run: \"uv run pytest -q\"\n""",
        encoding="utf-8",
    )
    extension_store = _extension_store(tmp_path)
    tools = _tools(tmp_path)
    runs = Mock(spec=FleetRunControlPlane)
    admitted = Mock(name="admitted")
    runs.start_prepared_plan.return_value = admitted
    service = FleetPlanService(
        FleetPlanResolver(extension_store, tools),
        runs,
        cast(ScheduleService, Mock()),
        extension_store,
        tools,
    )
    service.lock(plan_path)
    expected_instances = {"repo-a": "repo-instance-preview-a"}

    result = service.run(
        plan_path,
        repository_ids={"repo-a"},
        expected_repository_instance_ids=expected_instances,
    )

    assert result is admitted
    call = runs.start_prepared_plan.call_args
    assert call.kwargs["repository_ids"] == {"repo-a"}
    assert call.kwargs["expected_repository_instance_ids"] == expected_instances
