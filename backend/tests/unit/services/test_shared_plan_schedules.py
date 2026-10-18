from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.core.models.goon_plan_source import SharedPlanSource
from oh_goon.core.models.schedule import ScheduleCreate
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.schedules import scheduled_plan

ROOT = Path(__file__).resolve().parents[4]
STARTER = ROOT / "templates" / "plans" / "python" / "quality" / "plan.yml"


def test_create_resolves_shared_plan_in_backend_and_freezes_exact_source(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    document = store.write("python-quality", STARTER.read_text(encoding="utf-8"))
    resolved = scheduled_plan("quality")
    league = SimpleNamespace(
        repositories=Mock(return_value=(SimpleNamespace(repository_id="repo-a", repository_instance_id="instance-repo-a"),))
    )
    target = structural_fleet_plan_target(
        resolved,
        source=SharedPlanSource(plan_key="python-quality", sha256=document.sha256),
    )
    plans = SimpleNamespace(
        prepare_reviewed_execution_target=Mock(return_value=target),
        repository_instance_ids_for_target=Mock(
            return_value={"repo-a": "instance-repo-a"}
        ),
    )
    created = Mock(name="created")
    schedules = SimpleNamespace(create_prepared=Mock(return_value=created))
    service = SharedPlanScheduleService(store, league, plans, schedules)

    result = service.create(
        "nightly-quality",
        name="Nightly quality",
        repository_ids={"repo-a"},
        expected_repository_instance_ids={"repo-a": "instance-repo-a"},
        plan_key="python-quality",
        expected_plan_revision=document.sha256,
        expected_plan_id="reviewed-plan-id",
            expected_execution_digest="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        interval_minutes=60,
    )

    assert result is created
    plans.prepare_reviewed_execution_target.assert_called_once_with(
        store.path("python-quality"),
        expected_execution_digest="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        inputs=None,
        source=SharedPlanSource(plan_key="python-quality", sha256=document.sha256),
        expected_plan_id="reviewed-plan-id",
        root_expected_name="python-quality",
    )
    schedules.create_prepared.assert_called_once_with(
        "nightly-quality",
        ScheduleCreate(
            name="Nightly quality",
            repository_ids=("repo-a",),
            repository_instance_ids={"repo-a": "instance-repo-a"},
            target=target,
            interval_minutes=60,
        ),
    )


def test_create_rejects_repository_outside_current_league(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    store.write("python-quality", STARTER.read_text(encoding="utf-8"))
    plans = SimpleNamespace(prepare_reviewed_execution_target=Mock())
    schedules = SimpleNamespace(create_prepared=Mock())
    service = SharedPlanScheduleService(
        store,
        SimpleNamespace(repositories=Mock(return_value=())),
        plans,
        schedules,
    )

    with pytest.raises(FleetPlanInvalid, match="current managed League Team"):
        service.create(
            "nightly-quality",
            name="Nightly quality",
            repository_ids={"repo-a"},
            expected_repository_instance_ids={"repo-a": "instance-repo-a"},
            plan_key="python-quality",
            expected_plan_revision=store.read("python-quality").sha256,
            expected_plan_id="reviewed-plan-id",
            expected_execution_digest="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            interval_minutes=60,
        )

    plans.prepare_reviewed_execution_target.assert_not_called()
    schedules.create_prepared.assert_not_called()


def test_create_rejects_repository_instance_changed_after_schedule_review(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    document = store.write("python-quality", STARTER.read_text(encoding="utf-8"))
    plans = SimpleNamespace(prepare_reviewed_execution_target=Mock())
    schedules = SimpleNamespace(create_prepared=Mock())
    service = SharedPlanScheduleService(
        store,
        SimpleNamespace(repositories=Mock(return_value=(SimpleNamespace(repository_id="repo-a", repository_instance_id="instance-new"),))),
        plans,
        schedules,
    )

    with pytest.raises(FleetPlanSourceStale, match="repository selection changed after review"):
        service.create(
            "nightly-quality",
            name="Nightly quality",
            repository_ids={"repo-a"},
            expected_repository_instance_ids={"repo-a": "instance-reviewed"},
            plan_key="python-quality",
            expected_plan_revision=document.sha256,
            expected_plan_id="reviewed-plan-id",
            expected_execution_digest="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            interval_minutes=60,
        )

    plans.prepare_reviewed_execution_target.assert_not_called()
    schedules.create_prepared.assert_not_called()


def test_create_rejects_plan_changed_after_schedule_review(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    reviewed = store.write("python-quality", STARTER.read_text(encoding="utf-8"))
    store.write(
        "python-quality",
        STARTER.read_text(encoding="utf-8") + "\n# changed after review\n",
        expected_revision=reviewed.sha256,
    )
    plans = SimpleNamespace(prepare_reviewed_execution_target=Mock())
    schedules = SimpleNamespace(create_prepared=Mock())
    service = SharedPlanScheduleService(
        store,
        SimpleNamespace(repositories=Mock(return_value=(SimpleNamespace(repository_id="repo-a", repository_instance_id="instance-repo-a"),))),
        plans,
        schedules,
    )

    with pytest.raises(FleetPlanSourceStale, match="changed after Schedule review"):
        service.create(
            "nightly-quality",
            name="Nightly quality",
            repository_ids={"repo-a"},
            expected_repository_instance_ids={"repo-a": "instance-repo-a"},
            plan_key="python-quality",
            expected_plan_revision=reviewed.sha256,
            expected_plan_id="reviewed-plan-id",
            expected_execution_digest="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            interval_minutes=60,
        )

    plans.prepare_reviewed_execution_target.assert_not_called()
    schedules.create_prepared.assert_not_called()
