import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.core.execution_lock_identity import LOCK_VERSION
from oh_goon.core.models.goon_plan_source import SharedPlanSource
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.shared_plans import SharedPlanService
from tests.factories.shipped_templates import GOON_TEMPLATES as SHIPPED_GOONS
from tests.factories.shipped_templates import PLAN_TEMPLATES as SHIPPED_PLANS

ROOT = Path(__file__).resolve().parents[4]
STARTER = ROOT / "templates" / "plans" / "python" / "quality" / "plan.yml"


def _shared_quality() -> str:
    return STARTER.read_text(encoding="utf-8")


def test_shared_plan_store_digest_is_exact_file_bytes(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    path = store.path("byte-exact")
    path.parent.mkdir(parents=True)
    raw = b"version: 1\r\nname: byte-exact\r\n"
    path.write_bytes(raw)

    document = store.read("byte-exact")

    assert document.sha256 == hashlib.sha256(raw).hexdigest()
    assert document.byte_count == len(raw)


def test_existing_shared_plan_requires_the_exact_committed_sha(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    first_content = _shared_quality()
    first = store.write("python-quality", first_content)
    second_content = first_content + "\n# saved revision v2\n"

    with pytest.raises(FleetPlanSourceStale):
        store.write("python-quality", second_content)

    second = store.write("python-quality", second_content, expected_revision=first.sha256)
    assert second.sha256 != first.sha256
    assert store.read("python-quality") == second

    with pytest.raises(FleetPlanSourceStale):
        store.write("python-quality", first_content, expected_revision=first.sha256)
    assert store.read("python-quality") == second


def test_concurrent_shared_plan_writers_cannot_both_commit_same_sha(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    base = _shared_quality()
    first = store.write("python-quality", base)

    def save(label: str) -> str:
        content = base + f"\n# {label}\n"
        try:
            result = store.write("python-quality", content, expected_revision=first.sha256)
        except FleetPlanSourceStale:
            return "stale"
        return result.sha256

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(save, ("Writer A", "Writer B")))

    committed = tuple(outcome for outcome in outcomes if outcome != "stale")
    assert len(committed) == 1
    assert outcomes.count("stale") == 1
    assert store.read("python-quality").sha256 == committed[0]


def test_template_selection_materializes_canonical_shared_plan_and_is_idempotent(
    tmp_path: Path,
) -> None:
    from oh_goon.runtime.plan_templates import PlanTemplateStore

    store = SharedPlanFileStore(tmp_path / "plans")
    templates = PlanTemplateStore()
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), templates
    )

    template_sha = templates.read("python/quality").sha256
    first = service.create_from_template("python/quality", template_sha)
    second = service.create_from_template("python/quality", template_sha)

    assert first.plan_key == "python-quality"
    assert second == first
    assert store.read("python-quality") == first


def test_template_selection_rejects_changed_source_before_write(tmp_path: Path) -> None:
    from oh_goon.runtime.plan_templates import PlanTemplateStore

    store = SharedPlanFileStore(tmp_path / "plans")
    templates = PlanTemplateStore()
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), templates
    )

    with pytest.raises(FleetPlanSourceStale, match="changed; expected SHA-256"):
        service.create_from_template("python/quality", "0" * 64)

    assert store.list_keys() == ()


def test_template_selection_supports_explicit_destination_key(tmp_path: Path) -> None:
    from oh_goon.runtime.plan_templates import PlanTemplateStore

    store = SharedPlanFileStore(tmp_path / "plans")
    templates = PlanTemplateStore()
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), templates
    )
    template_sha = templates.read("python/quality").sha256

    document = service.create_from_template(
        "python/quality", template_sha, plan_key="shared-quality"
    )

    assert document.plan_key == "shared-quality"
    assert store.read("shared-quality") == document


def test_shared_plan_read_projects_typed_template_inputs_for_frontend(tmp_path: Path) -> None:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    store = SharedPlanFileStore(tmp_path / "plans")
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    service = SharedPlanService(
        store,
        SimpleNamespace(),
        SimpleNamespace(input_definitions_with_source=resolver.input_definitions_with_source),
        SimpleNamespace(),
        SHIPPED_PLANS,
    )
    templates = service.templates().templates
    template_sha = next(item.sha256 for item in templates if item.template_key == "python/quality")
    service.create_from_template("python/quality", template_sha)

    document = service.read("python-quality", include_inputs=True)

    assert {item.name for item in document.inputs} == {"project", "dependency_group", "typechecker"}
    definition = next(item for item in document.inputs if item.name == "typechecker")
    assert definition.input_type.value == "choice"
    assert definition.default == "pyright"
    assert definition.choices == ("pyright", "ty")


def test_template_selection_never_overwrites_diverged_shared_plan(tmp_path: Path) -> None:
    from oh_goon.runtime.plan_templates import PlanTemplateStore

    store = SharedPlanFileStore(tmp_path / "plans")
    templates = PlanTemplateStore()
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), templates
    )
    template_sha = templates.read("python/quality").sha256
    first = service.create_from_template("python/quality", template_sha)
    store.write(
        first.plan_key,
        first.content + "\n# operator customization\n",
        expected_revision=first.sha256,
    )

    with pytest.raises(FleetPlanSourceStale, match="differs from shipped template"):
        service.create_from_template("python/quality", template_sha)


def test_shared_plan_storage_key_is_the_canonical_plan_identity(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )

    document = service.save("python-quality", _shared_quality())

    assert document.plan_key == "python-quality"
    assert store.path("python-quality").name == "plan.yml"


def test_shared_plan_key_is_exact_and_never_normalized(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")

    for invalid in ("Python-Quality", "python_quality", "python.quality", " python-quality"):
        with pytest.raises(FleetPlanInvalid, match="lowercase letters, numbers"):
            store.path(invalid)
    with pytest.raises(ValueError, match="lowercase letters, numbers"):
        SharedPlanSource(plan_key="Python-Quality", sha256="0" * 64)


def test_shared_plan_cli_path_resolution_does_not_require_repository_goon(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    team_a = SimpleNamespace(repository_id="repo-a")
    team_b = SimpleNamespace(repository_id="repo-b")
    league = SimpleNamespace(resolve_repositories=Mock(return_value=(team_a, team_b)))
    service = SharedPlanService(
        store, league, SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
    repositories = (Path("/league/a/src"), Path("/league/b"))

    ids = service.repository_ids(repositories)

    assert ids == {"repo-a", "repo-b"}
    league.resolve_repositories.assert_called_once_with(repositories)


def test_shared_plan_preview_resolves_once_and_reports_each_team_without_repo_goon(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.fleet_plan_evidence import FleetPlanPreview

    store = SharedPlanFileStore(tmp_path / "plans")
    content = _shared_quality()
    store.write("python-quality", content)
    from oh_goon.core.models.repository import RepositoryIdentity

    repository_a = RepositoryIdentity.model_validate({"path": tmp_path / "a"})
    repository_b = RepositoryIdentity.model_validate({"path": tmp_path / "b"})
    repository_a.path.mkdir()
    repository_b.path.mkdir()
    (repository_a.path / ".git").mkdir()
    (repository_b.path / ".git").mkdir()
    league = SimpleNamespace(
        repositories=Mock(return_value=(repository_a, repository_b)), team=Mock()
    )
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    resolved, source_sha256 = resolver.resolve_with_source(
        store.path("python-quality"),
        inputs={"typechecker": "ty"},
        root_expected_name="python-quality",
    )
    plans = SimpleNamespace(
        resolve_with_source=Mock(return_value=(resolved, source_sha256)),
        preview_resolved=Mock(
            side_effect=[
                FleetPlanPreview(plan_id=resolved.plan_id, repository=repository_a.path, steps=()),
                FleetPlanInvalid("Dockerfile is missing"),
            ]
        ),
    )
    service = SharedPlanService(store, league, plans, SimpleNamespace(), SimpleNamespace())

    preview = service.preview(
        "python-quality",
        repository_ids={repository_a.repository_id, repository_b.repository_id},
        inputs={"typechecker": "ty"},
    )

    assert preview.plan_id == resolved.plan_id
    assert [(item.name, item.value) for item in preview.explicit_inputs] == [("typechecker", "ty")]
    assert preview.ready_count == 1
    assert preview.blocked_count == 1
    assert [item.state for item in preview.teams] == ["READY", "BLOCKED"]
    assert preview.teams[1].reason == "Dockerfile is missing"
    assert preview.teams[1].diagnostic.reason_code == "FLEET_PLAN_INVALID"
    assert preview.teams[1].diagnostic.authority_owner == "fleet_plan_preview"
    assert [fact.reason_code for fact in preview.readiness] == [
        "HOST_STORAGE_NOT_PROBED_IN_PREVIEW",
        "CACHE_REUSE_NOT_REQUESTED",
    ]
    plans.resolve_with_source.assert_called_once_with(
        store.path("python-quality"),
        inputs={"typechecker": "ty"},
        root_expected_name="python-quality",
    )
    assert plans.preview_resolved.call_count == 2
    league.team.assert_not_called()


def test_shared_plan_catalog_derives_last_run_and_revision_drift(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.core.vocabulary import FleetRunStatus

    store = SharedPlanFileStore(tmp_path / "plans")
    current = store.write("python-quality", _shared_quality())
    source = SharedPlanSource(plan_key="python-quality", sha256="0" * 64)
    target = FleetPlanTarget.model_construct(kind="fleet_plan", plan=Mock(), source=source)
    run = SimpleNamespace(
        fleet_run_id="fleet-old",
        accepted_at=datetime(2026, 8, 1, tzinfo=UTC),
        status=FleetRunStatus.COMPLETED,
        target=target,
        targets=(SimpleNamespace(), SimpleNamespace()),
    )
    page = SimpleNamespace(items=(SimpleNamespace(run=run),), next_before_revision=None)
    runs = SimpleNamespace(history=Mock(return_value=page))
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), runs, SimpleNamespace()
    )

    catalog = service.catalog()

    assert catalog.plans[0].sha256 == current.sha256
    assert catalog.plans[0].last_run is not None
    assert catalog.plans[0].last_run.fleet_run_id == "fleet-old"
    assert catalog.plans[0].last_run.team_count == 2
    assert catalog.plans[0].changed_since_last_run is True
    assert catalog.plans[0].shared_plan_id


def test_shared_plan_entire_league_snapshots_current_git_team_ids_without_repo_goon(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity

    store = SharedPlanFileStore(tmp_path / "plans")
    repository_a = RepositoryIdentity(path=tmp_path / "a")
    repository_b = RepositoryIdentity(path=tmp_path / "b")
    league = SimpleNamespace(
        repositories=Mock(return_value=(repository_a, repository_b)),
        team=Mock(),
        map=Mock(),
    )
    service = SharedPlanService(
        store, league, SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )

    repository_ids = service.all_repository_ids()

    assert repository_ids == {repository_a.repository_id, repository_b.repository_id}
    league.repositories.assert_called_once_with()
    league.team.assert_not_called()
    league.map.assert_not_called()


def test_shared_plan_write_returns_exact_committed_document_without_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    content = _shared_quality()

    def fail_second_read(plan_key: str):
        raise AssertionError(f"write must not reread committed plan {plan_key}")

    monkeypatch.setattr(store, "read", fail_second_read)

    committed = store.write("python-quality", content)

    disk = SharedPlanFileStore(store.root).read("python-quality")
    assert committed == disk


def test_shared_plan_cli_team_paths_resolve_from_one_league_state_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.services.league import LeagueService
    from oh_goon.services.repository_discovery import RepositoryDiscoveryService
    from tests.factories.local_state import local_state

    root = tmp_path / "league"
    api = root / "api"
    worker = root / "worker"
    for repository in (api, worker):
        repository.mkdir(parents=True)
        (repository / ".git").mkdir()
    nested = api / "src"
    nested.mkdir()
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    league = LeagueService(state, RepositoryDiscoveryService())
    expected = {
        league.repository(api).repository_id,
        league.repository(worker).repository_id,
    }
    original_read_view = state.read_view
    read_view = Mock(wraps=original_read_view)
    monkeypatch.setattr(state, "read_view", read_view)
    service = SharedPlanService(
        SharedPlanFileStore(tmp_path / "plans"),
        league,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    ids = service.repository_ids((nested, worker))

    assert ids == expected
    assert read_view.call_count == 1


def test_shared_plan_storage_refuses_external_local_catalog_before_write(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
    with pytest.raises(FleetPlanInvalid, match="file-backed Plan"):
        service.save("local", "version: 1\ngoons:\n- import: general/check\n  from: ./goons\n")
    assert not store.path("local").exists()


def test_shared_plan_history_pages_matching_runs_without_duplicates(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.core.vocabulary import FleetRunStatus

    store = SharedPlanFileStore(tmp_path / "plans")
    store.write("python-quality", _shared_quality())

    def item(revision: int, plan_key: str):
        source = SharedPlanSource(plan_key=plan_key, sha256=f"{revision:064x}")
        target = FleetPlanTarget.model_construct(kind="fleet_plan", plan=Mock(), source=source)
        run = SimpleNamespace(
            fleet_run_id=f"fleet-{revision}",
            accepted_at=datetime(2026, 8, 1, 12, revision % 60, tzinfo=UTC),
            status=FleetRunStatus.COMPLETED,
            target=target,
            targets=(SimpleNamespace(),),
        )
        return SimpleNamespace(admitted_revision=revision, run=run)

    items = (
        item(12, "python-quality"),
        item(11, "other-plan"),
        item(9, "python-quality"),
        item(8, "other-plan"),
        item(7, "python-quality"),
        item(5, "python-quality"),
    )

    class Runs:
        def history(self, *, limit: int, before_revision: int | None = None):
            visible = tuple(
                candidate
                for candidate in items
                if before_revision is None or candidate.admitted_revision < before_revision
            )[:limit]
            return SimpleNamespace(items=visible, next_before_revision=None)

    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), Runs(), SimpleNamespace()
    )

    first = service.history("python-quality", limit=2)
    second = service.history("python-quality", limit=2, before_revision=first.next_before_revision)

    assert [run.fleet_run_id for run in first.runs] == ["fleet-12", "fleet-9"]
    assert first.next_before_revision == 9
    assert [run.fleet_run_id for run in second.runs] == ["fleet-7", "fleet-5"]
    assert second.next_before_revision is None
    assert not (
        {run.fleet_run_id for run in first.runs} & {run.fleet_run_id for run in second.runs}
    )


def test_shared_plan_history_rejects_invalid_cursor_before_reading_runs(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    store.write("python-quality", _shared_quality())
    runs = SimpleNamespace(history=Mock())
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), runs, SimpleNamespace()
    )

    with pytest.raises(FleetPlanInvalid, match="cursor must be at least 1"):
        service.history("python-quality", before_revision=0)

    runs.history.assert_not_called()


def test_shared_plan_lock_preview_renders_exact_resolved_execution_without_writing_lock(
    tmp_path: Path,
) -> None:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    store = SharedPlanFileStore(tmp_path / "plans")
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)
    service = SharedPlanService(
        store,
        SimpleNamespace(),
        SimpleNamespace(resolve_with_source=resolver.resolve_with_source),
        SimpleNamespace(),
        SHIPPED_PLANS,
    )
    template_sha = next(
        item.sha256
        for item in service.templates().templates
        if item.template_key == "python/quality"
    )
    service.create_from_template("python/quality", template_sha)

    preview = service.lock_preview(
        "python-quality",
        inputs={"typechecker": "ty"},
    )

    expected = resolver.resolve(
        store.path("python-quality"),
        inputs={"typechecker": "ty"},
        root_expected_name="python-quality",
    )
    assert preview.plan_key == "python-quality"
    assert preview.plan_id == expected.plan_id
    lock = yaml.safe_load(preview.content)
    assert lock["lock_version"] == LOCK_VERSION
    assert lock["digest"] == preview.execution_digest
    assert not (store.path("python-quality").parent / "goon.lock").exists()


def test_shared_plan_state_is_owner_private_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows confidentiality relies on the user profile/custom-path ACL")
    root = tmp_path / "config" / "plans"
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    store = SharedPlanFileStore(root)
    document = store.write("python-quality", _shared_quality())

    assert document.plan_key == "python-quality"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "python-quality").stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path("python-quality").stat().st_mode) == 0o600


def test_shared_plan_preview_rejects_plan_revision_that_changed_after_read(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", _shared_quality())
    from oh_goon.core.models.repository import RepositoryIdentity

    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    league = SimpleNamespace(repositories=Mock(return_value=(repository,)), team=Mock())
    plans = SimpleNamespace(resolve=Mock(), preview_resolved=Mock())
    service = SharedPlanService(store, league, plans, SimpleNamespace(), SimpleNamespace())
    store.write(
        "python-quality",
        _shared_quality().replace("python/quality", "python/test"),
        expected_revision=original.sha256,
    )

    with pytest.raises(FleetPlanSourceStale, match="changed since it was read"):
        service.preview(
            "python-quality",
            repository_ids={repository.repository_id},
            expected_revision=original.sha256,
        )

    plans.resolve.assert_not_called()


def test_shared_plan_lock_preview_rejects_plan_revision_that_changed_after_read(
    tmp_path: Path,
) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", _shared_quality())
    service = SharedPlanService(
        store,
        SimpleNamespace(),
        SimpleNamespace(resolve=Mock()),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    store.write(
        "python-quality",
        _shared_quality().replace("python/quality", "python/test"),
        expected_revision=original.sha256,
    )

    with pytest.raises(FleetPlanSourceStale, match="changed since it was read"):
        service.lock_preview("python-quality", expected_revision=original.sha256)


def test_shared_plan_run_rejects_exact_source_revision_change_even_before_execution(
    tmp_path: Path,
) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", _shared_quality())
    plans = SimpleNamespace(run=Mock(), require_plan_ingress_preparation=Mock())
    service = SharedPlanService(
        store, SimpleNamespace(), plans, SimpleNamespace(), SimpleNamespace()
    )
    changed = store.write(
        "python-quality",
        _shared_quality() + "\n# source-only revision change\n",
        expected_revision=original.sha256,
    )
    assert changed.sha256 != original.sha256
    with pytest.raises(FleetPlanSourceStale, match="changed since it was read"):
        service.run(
            "python-quality",
            repository_ids={"repo-a"},
            fleet_run_id="fleet-stale-plan-source",
            expected_plan_id="plan-id-that-could-still-match",
            expected_execution_digest="sha256:" + "d" * 64,
            expected_plan_revision=original.sha256,
            repository_instance_ids={"repo-a": "instance-a"},
        )
    plans.run.assert_not_called()


def test_shared_plan_save_validates_exact_content_once_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.services.shared_plans as shared_plans_module

    store = SharedPlanFileStore(tmp_path / "plans")
    service = SharedPlanService(
        store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
    real_parse = shared_plans_module.parse_plan_template
    calls: list[str] = []

    def counted_parse(content: str, *, source: str, expected_name: str | None = None):
        calls.append(source)
        return real_parse(content, source=source, expected_name=expected_name)

    monkeypatch.setattr(shared_plans_module, "parse_plan_template", counted_parse)

    document = service.save("python-quality", _shared_quality())

    assert document.content == _shared_quality()
    assert calls == ["shared:python-quality/plan.yml"]


def test_shared_plan_input_projection_delegates_parsing_to_plan_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.services.shared_plans as shared_plans_module

    store = SharedPlanFileStore(tmp_path / "plans")
    document = store.write("python-quality", _shared_quality())
    input_definitions = Mock(return_value=((), document.sha256))
    service = SharedPlanService(
        store,
        SimpleNamespace(),
        SimpleNamespace(input_definitions_with_source=input_definitions),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    monkeypatch.setattr(
        shared_plans_module,
        "parse_plan_template",
        Mock(side_effect=AssertionError("service must not reparse before input projection")),
    )

    projected = service.read("python-quality", include_inputs=True)

    assert projected.sha256 == document.sha256
    input_definitions.assert_called_once_with(
        store.path("python-quality"), root_expected_name="python-quality"
    )
