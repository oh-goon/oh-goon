from pathlib import Path
from typing import cast

from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService


def test_shared_plan_run_still_preserves_explicit_multi_repository_scope(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - id: tests
    run: "uv run pytest -q"
""",
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    class RecordingRuns:
        def require_plan_ingress_preparation(self, run_id):
            assert run_id is None

        def start_prepared_plan(self, target, **kwargs):
            observed.update(kwargs)
            return object()

    service = FleetPlanService(
        FleetPlanResolver(),
        cast(FleetRunControlPlane, RecordingRuns()),
        cast(ScheduleService, object()),
        cast(object, object()),
        cast(object, object()),
    )

    service.lock(plan_path)
    service.run(
        plan_path,
        repository_ids={"repo-a", "repo-b", "repo-c"},
    )

    assert observed["repository_ids"] == {"repo-a", "repo-b", "repo-c"}
    assert observed["all_repositories"] is False
