from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.shared_plans import SharedPlanService
from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService
from tests.factories.shipped_templates import GOON_TEMPLATES as SHIPPED_GOONS


SHARED_PLAN = """version: 1
inputs:
- release_channel
- revision
goons:
- python/release-metadata
"""


def _repository(path: Path, *, with_owned_goon: bool) -> RepositoryIdentity:
    path.mkdir()
    (path / ".git").mkdir()
    (path / "pyproject.toml").write_text(
        f"""[project]
name = "{path.name}"
version = "1.2.3"
requires-python = ">=3.11"

[dependency-groups]
dev = []
""",
        encoding="utf-8",
    )
    (path / "uv.lock").write_text(
        f"""version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "{path.name}"
version = "1.2.3"
source = {{ editable = "." }}

[package.metadata]

[package.metadata.requires-dev]
dev = []
""",
        encoding="utf-8",
    )
    if with_owned_goon:
        (path / "goon.yml").write_text(
            "version: 1\nsteps:\n- run: definitely-must-not-be-resolved\n", encoding="utf-8"
        )
    return RepositoryIdentity(path=path)


def test_real_shared_plan_preview_uses_one_plan_across_heterogeneous_repositories(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "shared-plans")
    document = store.write("release-shared", SHARED_PLAN)
    owned = _repository(tmp_path / "owned-goon", with_owned_goon=True)
    no_goon = _repository(tmp_path / "no-goon", with_owned_goon=False)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    plans = FleetPlanService(
        resolver,
        Mock(),
        Mock(),
        Mock(),
        Mock(),
    )
    league = SimpleNamespace(repositories=Mock(return_value=(owned, no_goon)))
    service = SharedPlanService(store, league, plans, Mock(), Mock())

    preview = service.preview(
        "release-shared",
        expected_revision=document.sha256,
        repository_ids={owned.repository_id, no_goon.repository_id},
        inputs={"release_channel": "beta", "revision": "abcdef1"},
    )

    assert preview.sha256 == document.sha256
    assert preview.ready_count == 2
    assert preview.blocked_count == 0
    assert {team.repository.repository_id for team in preview.teams} == {
        owned.repository_id,
        no_goon.repository_id,
    }
    assert all(team.state == "READY" for team in preview.teams)
    assert all(team.preview.plan_id == preview.plan_id for team in preview.teams)
    assert all(team.preview.imports[0].applicability == "APPLICABLE" for team in preview.teams)
    assert all(team.preview.steps[0].disposition == "READY" for team in preview.teams)


def test_shared_plan_preview_identity_authorizes_exact_run_without_source_adjacent_lock(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "shared-plans")
    document = store.write("release-shared", SHARED_PLAN)
    repository = _repository(tmp_path / "team", with_owned_goon=False)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    admitted = SimpleNamespace(fleet_run_id="shared-run")
    runs = Mock(); runs.start_prepared_plan.return_value = admitted
    plans = FleetPlanService(resolver, runs, Mock(), Mock(), Mock())
    service = SharedPlanService(store, SimpleNamespace(repositories=Mock(return_value=(repository,))), plans, Mock(), Mock())
    inputs = {"release_channel": "beta", "revision": "abcdef1"}
    preview = service.preview("release-shared", expected_revision=document.sha256, repository_ids={repository.repository_id}, inputs=inputs)
    assert preview.teams[0].preview.imports[0].applicability == "APPLICABLE"
    assert preview.teams[0].preview.steps[0].disposition == "READY"
    instances = {team.repository.repository_id: team.repository_instance_id for team in preview.teams}
    result = service.run("release-shared", repository_ids={repository.repository_id}, inputs=inputs, expected_plan_revision=document.sha256, expected_plan_id=preview.plan_id, expected_execution_digest=preview.execution_digest, repository_instance_ids=instances)
    assert result is admitted
    assert not (store.path("release-shared").parent / "goon.lock").exists()
    call = runs.start_prepared_plan.call_args
    assert call.args[0].identity.digest == preview.execution_digest
    assert call.kwargs["expected_repository_instance_ids"] == instances


def test_shared_plan_preview_identity_creates_schedule_without_source_adjacent_lock(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "shared-plans")
    document = store.write("release-shared", SHARED_PLAN)
    repository = _repository(tmp_path / "team-schedule", with_owned_goon=False)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    runs = Mock()
    plans = FleetPlanService(resolver, runs, Mock(), Mock(), Mock())
    league = SimpleNamespace(repositories=Mock(return_value=(repository,)))
    shared = SharedPlanService(store, league, plans, Mock(), Mock())
    inputs = {"release_channel": "beta", "revision": "abcdef1"}
    preview = shared.preview(
        "release-shared",
        expected_revision=document.sha256,
        repository_ids={repository.repository_id},
        inputs=inputs,
    )
    instances = {
        team.repository.repository_id: team.repository_instance_id for team in preview.teams
    }
    runs.repository_instance_ids_for_prepared_plan.return_value = instances
    created = SimpleNamespace(schedule_id="nightly")
    schedules = Mock()
    schedules.create_prepared.return_value = created
    schedule_league = SimpleNamespace(
        repositories=Mock(
            return_value=(
                SimpleNamespace(
                    repository_id=repository.repository_id,
                    repository_instance_id=instances[repository.repository_id],
                ),
            )
        )
    )
    service = SharedPlanScheduleService(store, schedule_league, plans, schedules)

    result = service.create(
        "nightly",
        name="Nightly",
        repository_ids=(repository.repository_id,),
        expected_repository_instance_ids=instances,
        plan_key="release-shared",
        expected_plan_revision=document.sha256,
        expected_plan_id=preview.plan_id,
        expected_execution_digest=preview.execution_digest,
        interval_minutes=60,
        inputs=inputs,
    )

    assert result is created
    assert not (store.path("release-shared").parent / "goon.lock").exists()
    target = schedules.create_prepared.call_args.args[1].target
    assert target.identity.digest == preview.execution_digest


def test_shared_plan_run_rejects_execution_identity_from_different_inputs_before_admission(tmp_path: Path) -> None:
    import pytest
    from oh_goon.core.errors import FleetPlanInvalid
    store = SharedPlanFileStore(tmp_path / "shared-plans"); document = store.write("release-shared", SHARED_PLAN)
    repository = _repository(tmp_path / "team", with_owned_goon=False)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS); runs = Mock(); plans = FleetPlanService(resolver, runs, Mock(), Mock(), Mock())
    service = SharedPlanService(store, SimpleNamespace(repositories=Mock(return_value=(repository,))), plans, Mock(), Mock())
    preview = service.preview("release-shared", expected_revision=document.sha256, repository_ids={repository.repository_id}, inputs={"release_channel":"beta","revision":"abcdef1"})
    instances = {team.repository.repository_id: team.repository_instance_id for team in preview.teams}
    with pytest.raises(FleetPlanInvalid, match="execution identity changed after preview"):
        service.run("release-shared", repository_ids={repository.repository_id}, inputs={"release_channel":"beta","revision":"abcdef2"}, expected_plan_revision=document.sha256, expected_plan_id=preview.plan_id, expected_execution_digest=preview.execution_digest, repository_instance_ids=instances)
    runs.start_prepared_plan.assert_not_called()
