import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryFile
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.goon_plan_source import SharedPlanSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.shared_plan import SharedPlanDocument
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import (
    new_fleet_run_id,
    repository_id,
    schedule_repository_fleet_run_id,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunSource, ScheduleExecutionStatus
from oh_goon.runtime import goon_templates
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService
from oh_goon.services.shared_plans import SharedPlanService
from tests.factories.shipped_templates import GOON_TEMPLATES as SHIPPED_GOONS
from tests.integration.api.client import authenticated_api_client

SHARED_PLAN = """version: 1
inputs:
- release_channel
- revision
goons:
- python/release-metadata
"""


@dataclass(frozen=True)
class _NativeSharedWorkflow:
    runtime: ApplicationRuntime
    root: Path
    repositories: tuple[Path, Path]
    decoy: Path
    catalog: Path
    document: SharedPlanDocument
    environment: dict[str, str]


@pytest.fixture
def native_shared_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_NativeSharedWorkflow]:
    if os.name != "posix" or shutil.which("git") is None:
        pytest.skip("native POSIX Git and commands required")
    environment = {
        "OH_GOON_DB": str(tmp_path / "runtime.sqlite"),
        "OH_GOON_LEAGUE_FILE": str(tmp_path / "league.yml"),
        "OH_GOON_SHARED_PLAN_DIR": str(tmp_path / "saved-plans"),
        "OH_GOON_LINEUP_DIR": str(tmp_path / "lineups"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    repositories = (tmp_path / "invalid-root-goon", tmp_path / "missing-root-goon")
    decoy = tmp_path / "unselected"
    for repository in (*repositories, decoy):
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
        (repository / "identity.txt").write_text(repository.name + "\n")
        (repository / "probe.py").write_text(
            "from pathlib import Path\n"
            "identity = Path('identity.txt').read_text()\n"
            "with Path('result.txt').open('a') as output:\n"
            "    output.write(identity)\n"
            "print(identity, end='')\n"
        )
    (repositories[0] / "goon.yml").write_text("steps: [\n")
    LeagueFolderStore().replace((*repositories, decoy))

    # As in the package lifecycle fixture, give the original source provider an
    # actual catalog. Parsing, source identity and execution are never replaced.
    catalog = tmp_path / "catalog"
    package = catalog / "general/shared-probe"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/shared-probe\n\nRecord the selected target.\n")
    (package / "goon.yml").write_text(
        "steps:\n- id: selected-target\n  run: "
        + json.dumps(shlex.join((sys.executable, "probe.py")))
        + "\n"
        "  outputs:\n    IDENTITY: {stream: stdout, max_bytes: 64}\n"
        "  artifacts:\n  - path: result.txt\n"
    )
    monkeypatch.setattr(goon_templates, "default_goon_templates_root", lambda: catalog)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.lineups.save("selected", name="Selected", repositories=repositories)
        document = runtime.shared_plan.save("selected-shared", "goons:\n- general/shared-probe\n")
        yield _NativeSharedWorkflow(
            runtime, tmp_path, repositories, decoy, catalog, document, dict(os.environ)
        )
    finally:
        runtime.shutdown(wait_for_runs=True)


def _native_json(case: _NativeSharedWorkflow, *argv: str) -> dict[str, Any]:
    # These are external app consumers with explicitly selected state, not Step
    # children (the product command owner deliberately strips app control env).
    with TemporaryFile() as output, TemporaryFile() as errors:
        result = subprocess.run(
            argv,
            cwd=case.root,
            env=case.environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=30,
        )
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(256 * 1024 + 1), errors.read(256 * 1024 + 1)
    assert len(stdout) <= 256 * 1024 and len(stderr) <= 256 * 1024
    assert result.returncode == 0, stderr.decode("utf-8", errors="replace")
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    return payload


def _fresh_shared_readback(
    case: _NativeSharedWorkflow, details: tuple[FleetRunDetail, ...]
) -> dict[str, Any]:
    # A separate installed process enters only the original observation runtime
    # and authenticated HTTP routes. There is no live execution object to reuse.
    api = _native_json(
        case,
        sys.executable,
        "-I",
        "-c",
        """
import json, sys
from contextlib import closing
from fastapi.testclient import TestClient
from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.api.local_access import LOCAL_ACCESS_HEADER
from oh_goon.application import ApplicationRuntime

runtime = ApplicationRuntime.build(startup_purpose='observation')
try:
    app = create_app(local_access_token='shared-cutover-readback-test')
    app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: runtime.runs
    app.dependency_overrides[dependencies.schedule_service] = lambda: runtime.schedules
    with closing(TestClient(app, headers={LOCAL_ACCESS_HEADER: app.state.local_access_token})) as client:
        details = {}
        for run_id in sys.argv[1:]:
            response = client.get('/api/fleet/runs/' + run_id)
            assert response.status_code == 200, response.text
            details[run_id] = response.json()
        schedules = client.get('/api/schedules')
        assert schedules.status_code == 200, schedules.text
        print(json.dumps({'details': details, 'schedules': schedules.json()}))
finally:
    runtime.shutdown()
""",
        *(detail.run.fleet_run_id for detail in details),
    )
    cli = str(Path(sys.executable).with_name("oh-goon"))
    for detail in details:
        run_id = detail.run.fleet_run_id
        observed = api["details"][run_id]
        assert observed["run"] == detail.run.model_dump(mode="json")
        assert observed["execution"] == detail.execution.model_dump(mode="json")
        assert observed["operations"] == [
            report.model_dump(mode="json") for report in detail.operations
        ]
        # Later sibling Runs advance the original global read cut without
        # rewriting this child's recorded execution, result or operation truth.
        assert observed["journal_revision"] >= detail.journal_revision
        shown = _native_json(case, cli, "runs", "show", run_id, "--json")
        tape = _native_json(case, cli, "runs", "tape", run_id, "--json")
        events = _native_json(case, cli, "runs", "events", run_id, "--json")
        for reply in (shown, tape, events):
            assert reply.pop("_cli") == {
                "schema": "oh-goon.cli/v1",
                "exit_code": 0,
                "exit_kind": "SUCCESS",
            }
        assert shown["status"] == detail.run.status.value == "COMPLETED"
        assert shown["outcome"] == detail.outcome.value == "SUCCESS"
        assert tape == api["details"][run_id]["game_tape"]
        assert events["run"]["target"] == api["details"][run_id]["run"]["target"]
        assert {item["repository"]["repository_id"] for item in tape["repositories"]} == {
            target.repository_id for target in detail.run.targets
        }
    return api


def test_saved_shared_plan_lineup_executes_exact_targets_and_reopens_cli_api_history(
    native_shared_workflow: _NativeSharedWorkflow,
) -> None:
    case = native_shared_workflow
    runtime, document = case.runtime, case.document
    selected = runtime.lineups.repository_ids("selected")
    assert selected == {repository_id(path) for path in case.repositories}
    app = create_app(local_access_token="shared-cutover-execution-test")
    app.dependency_overrides[dependencies.shared_plan_service] = lambda: runtime.shared_plan
    with closing(authenticated_api_client(app)) as client:
        preview = client.post(
            "/api/league/shared-plan-previews",
            json={
                "plan_key": document.plan_key,
                "expected_revision": document.sha256,
                "repository_ids": sorted(selected),
            },
        )
        assert preview.status_code == 200, preview.text
        reviewed = preview.json()
        assert reviewed["ready_count"] == 2 and reviewed["blocked_count"] == 0
        admitted = client.post(
            "/api/league/shared-plan-runs",
            json={
                "fleet_run_id": new_fleet_run_id(),
                "plan_key": document.plan_key,
                "repository_ids": sorted(selected),
                "expected_plan_revision": document.sha256,
                "expected_plan_id": reviewed["plan_id"],
                "expected_execution_digest": reviewed["execution_digest"],
                "repository_instance_ids": {
                    team["repository"]["repository_id"]: team["repository_instance_id"]
                    for team in reviewed["teams"]
                },
            },
        )
        assert admitted.status_code == 202, admitted.text
        detail = runtime.runs.wait_for_terminal(admitted.json()["fleet_run_id"], timeout=20)
    assert isinstance(detail.run.target, FleetPlanTarget)
    assert detail.run.target.source == SharedPlanSource(
        plan_key=document.plan_key, sha256=document.sha256
    )
    assert {target.repository_id for target in detail.run.targets} == selected
    assert len(detail.operations) == 2
    runtime.shutdown(wait_for_runs=True)
    api = _fresh_shared_readback(case, (detail,))
    assert api["schedules"]["schedules"] == []
    for repository in case.repositories:
        assert (repository / "result.txt").read_text() == repository.name + "\n"
    assert not (case.decoy / "result.txt").exists()


def test_saved_shared_schedule_ticks_frozen_source_once_per_selected_target_after_restart(
    native_shared_workflow: _NativeSharedWorkflow, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = native_shared_workflow
    runtime, document = case.runtime, case.document
    selected = runtime.lineups.repository_ids("selected")
    preview = runtime.shared_plan.preview(document.plan_key, repository_ids=selected)
    # Supply only the creation clock to the original definition owner. The
    # native scheduler decides due time and performs every admission/transition.
    created_at = utc_now() - timedelta(minutes=2)
    with monkeypatch.context() as clock:
        clock.setattr("oh_goon.runtime.schedule_definitions.utc_now", lambda: created_at)
        saved = runtime.shared_plan_schedules.create(
            "saved-shared",
            name="Saved shared selection",
            plan_key=document.plan_key,
            repository_ids=tuple(sorted(selected)),
            expected_repository_instance_ids={
                team.repository.repository_id: team.repository_instance_id
                for team in preview.teams
                if team.state == "READY"
            },
            expected_plan_revision=document.sha256,
            expected_plan_id=preview.plan_id,
            expected_execution_digest=preview.execution_digest,
            interval_minutes=1,
        )
    runtime.shutdown(wait_for_runs=True)
    shutil.rmtree(case.catalog)
    shutil.rmtree(runtime.shared_plan.root)
    cold = ApplicationRuntime.build(standard_local=True)
    try:
        assert cold.schedules.definition(saved.schedule_id) == saved
        assert cold.lineups.repository_ids("selected") == selected
        cold.schedules.tick()
        occurrence = cold.schedules.catalog().schedules[0].latest_occurrence
        assert occurrence is not None and occurrence.expected == 2
        details = []
        for target_id in sorted(selected):
            run_id = schedule_repository_fleet_run_id(occurrence.schedule_run_id, target_id)
            detail = cold.runs.wait_for_terminal(run_id, timeout=20)
            assert detail.run.source is FleetRunSource.SCHEDULED
            assert isinstance(detail.run.target, FleetPlanTarget)
            assert detail.run.target.source == saved.target.source
            assert {target.repository_id for target in detail.run.targets} == {target_id}
            details.append(detail)
            # Original Schedule execution observes the terminal child, then
            # admits the next pending target. No custom fan-out or clock loop.
            cold.schedules.tick()
            cold.schedules.tick()
        observed = cold.schedules.catalog().schedules[0].latest_occurrence
        assert observed is not None
        assert observed.schedule_run_id == occurrence.schedule_run_id
        assert observed.status is ScheduleExecutionStatus.COMPLETED
        assert observed.successful == 2
        assert len(cold.runs.history().items) == 2
        assert cold.schedules.definition(saved.schedule_id) == saved
    finally:
        cold.shutdown(wait_for_runs=True)
    api = _fresh_shared_readback(case, tuple(details))
    retained = api["schedules"]["schedules"][0]
    assert retained["definition"] == saved.model_dump(mode="json")
    assert retained["latest_occurrence"]["schedule_run_id"] == occurrence.schedule_run_id
    assert retained["latest_occurrence"]["successful"] == 2
    for repository in case.repositories:
        assert (repository / "result.txt").read_text() == repository.name + "\n"
    assert not (case.decoy / "result.txt").exists()
    assert not case.catalog.exists()


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


def test_real_shared_plan_preview_uses_one_plan_across_heterogeneous_repositories(
    tmp_path: Path,
) -> None:
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


def test_shared_plan_preview_identity_authorizes_exact_run_without_source_adjacent_lock(
    tmp_path: Path,
) -> None:
    store = SharedPlanFileStore(tmp_path / "shared-plans")
    document = store.write("release-shared", SHARED_PLAN)
    repository = _repository(tmp_path / "team", with_owned_goon=False)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    admitted = SimpleNamespace(fleet_run_id="shared-run")
    runs = Mock()
    runs.start_prepared_plan.return_value = admitted
    plans = FleetPlanService(resolver, runs, Mock(), Mock(), Mock())
    service = SharedPlanService(
        store, SimpleNamespace(repositories=Mock(return_value=(repository,))), plans, Mock(), Mock()
    )
    inputs = {"release_channel": "beta", "revision": "abcdef1"}
    preview = service.preview(
        "release-shared",
        expected_revision=document.sha256,
        repository_ids={repository.repository_id},
        inputs=inputs,
    )
    assert preview.teams[0].preview.imports[0].applicability == "APPLICABLE"
    assert preview.teams[0].preview.steps[0].disposition == "READY"
    instances = {
        team.repository.repository_id: team.repository_instance_id for team in preview.teams
    }
    result = service.run(
        "release-shared",
        repository_ids={repository.repository_id},
        inputs=inputs,
        expected_plan_revision=document.sha256,
        expected_plan_id=preview.plan_id,
        expected_execution_digest=preview.execution_digest,
        repository_instance_ids=instances,
    )
    assert result is admitted
    assert not (store.path("release-shared").parent / "goon.lock").exists()
    call = runs.start_prepared_plan.call_args
    assert call.args[0].identity.digest == preview.execution_digest
    assert call.kwargs["expected_repository_instance_ids"] == instances


def test_shared_plan_preview_identity_creates_schedule_without_source_adjacent_lock(
    tmp_path: Path,
) -> None:
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
    schedule_league = SimpleNamespace(repositories=Mock(return_value=(repository,)))
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


def test_shared_plan_run_rejects_execution_identity_from_different_inputs_before_admission(
    tmp_path: Path,
) -> None:
    import pytest

    from oh_goon.core.errors import FleetPlanInvalid

    store = SharedPlanFileStore(tmp_path / "shared-plans")
    document = store.write("release-shared", SHARED_PLAN)
    repository = _repository(tmp_path / "team", with_owned_goon=False)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    runs = Mock()
    plans = FleetPlanService(resolver, runs, Mock(), Mock(), Mock())
    service = SharedPlanService(
        store, SimpleNamespace(repositories=Mock(return_value=(repository,))), plans, Mock(), Mock()
    )
    preview = service.preview(
        "release-shared",
        expected_revision=document.sha256,
        repository_ids={repository.repository_id},
        inputs={"release_channel": "beta", "revision": "abcdef1"},
    )
    instances = {
        team.repository.repository_id: team.repository_instance_id for team in preview.teams
    }
    with pytest.raises(FleetPlanInvalid, match="execution identity changed after preview"):
        service.run(
            "release-shared",
            repository_ids={repository.repository_id},
            inputs={"release_channel": "beta", "revision": "abcdef2"},
            expected_plan_revision=document.sha256,
            expected_plan_id=preview.plan_id,
            expected_execution_digest=preview.execution_digest,
            repository_instance_ids=instances,
        )
    runs.start_prepared_plan.assert_not_called()
