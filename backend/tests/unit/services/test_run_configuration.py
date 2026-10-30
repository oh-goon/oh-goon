from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from oh_goon.core.errors import ExecutionLockRejected, FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.repository import ExcludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.models.run_configuration import RunConfiguration
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetScope
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.run_configuration import (
    capture_run_configuration_source,
    load_run_configuration,
    normalize_run_configuration,
)


def _configuration(tmp_path: Path, *, source: str = "{kind: file-plan, path: ./plan.yml}"):
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    identity = RepositoryIdentity(path=repository)
    path = tmp_path / "run.yml"
    path.write_text(
        f"version: 2\nsource: {source}\ntarget: {{repository: repository}}\n",
        encoding="utf-8",
    )
    return path, identity


def _plan(tmp_path: Path):
    package = tmp_path / "goons/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n", encoding="utf-8")
    (package / "goon.yml").write_text(
        "version: 1\ninputs:\n  count: {type: integer, default: 3}\n"
        "steps:\n  - id: check\n    run: 'echo ${{ inputs.count }}'\n",
        encoding="utf-8",
    )
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ngoons:\n  - import: general/check\n", encoding="utf-8")
    return path, FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))


def test_capture_keeps_selected_values_and_requires_original_file_identity(tmp_path):
    path, _ = _configuration(tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("inputs: {count: 7}\n")
    captured = load_run_configuration(path)
    assert captured.inputs == {"count": 7}
    assert captured.external_artifacts() == (("run.yml", path.read_bytes()),)
    path.write_text(path.read_text().replace("count: 7", "count: 8"), encoding="utf-8")
    assert captured.inputs == {"count": 7}
    with pytest.raises(FleetPlanInvalid, match="changed"):
        captured.require_fresh()


@pytest.mark.parametrize(
    "source",
    [
        "{kind: file-plan, path: ./plan.yml}",
        "{kind: catalog-plan, name: python/quality}",
        "{kind: repository-goon}",
    ],
)
def test_missing_run_lock_refuses_before_local_or_catalog_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    path, repository = _configuration(tmp_path, source=source)
    resolver = FleetPlanResolver()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Missing Run authority reached source analysis")

    monkeypatch.setattr(resolver, "analyze_source", forbidden)
    with pytest.raises(ExecutionLockRejected) as refused:
        capture_run_configuration_source(
            load_run_configuration(path), (repository,), resolver, allow_network=True
        )
    assert refused.value.reason == "missing"


@pytest.mark.parametrize(
    "content",
    [
        "version: 2\nversion: 2\nsource: {kind: repository-goon}\ntarget: {repository: .}",
        "version: 2\nsource: {kind: file-plan, path: plan.yml, name: python/quality}\n"
        "target: {repository: .}",
        "version: 2\nsource: {kind: repository-goon}\ntarget: {repository: .}\nsteps: []",
        "version: true\nsource: {kind: repository-goon}\ntarget: {repository: .}",
        "version: 1\nsource: {kind: repository-goon}\ntarget: {repository: .}",
        "version: 2\nsource: {kind: repository-goon}\ntarget: {}",
        "version: 2\nsource: {kind: repository-goon}\ntarget: {repository: .}\n"
        "inputs: {nested: {secret: do-not-echo}}",
        "version: 2\nsource: {kind: repository-goon}\ntarget: {repository: .}\n"
        "inputs: {value: '${SECRET}'}",
        "version: 2\nsource: &source {kind: repository-goon}\ntarget: {repository: .}",
    ],
)
def test_unsupported_or_ambiguous_configuration_is_refused(tmp_path, content):
    path = tmp_path / "run.yml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(FleetPlanInvalid) as error:
        load_run_configuration(path)
    assert "do-not-echo" not in str(error.value)
    assert "SECRET" not in str(error.value)


def test_only_registered_target_is_selected_without_path_guessing(tmp_path):
    path, repository = _configuration(tmp_path)
    (tmp_path / "plan.yml").write_text("version: 1\ngoons: []\n", encoding="utf-8")
    captured = load_run_configuration(path)
    with pytest.raises(FleetPlanInvalid, match="registered repository"):
        normalize_run_configuration(captured, [])
    normalized = normalize_run_configuration(captured, [repository])
    assert normalized.repository == repository
    assert normalized.plan_path == tmp_path / "plan.yml"
    with pytest.raises(FleetPlanInvalid, match="exactly one"):
        normalize_run_configuration(captured, [repository, repository])


@pytest.mark.parametrize(
    "target",
    [
        {"repository_id": "repo-one"},
        {"repository_ids": ["repo-one", "repo-two"]},
        {"repository": None, "repositories": ["repo-one", "repo-two"]},
        {"repository": "repo-one", "repositories": None},
        {"repositories": []},
        {"repositories": ["repo-one"]},
        {"repositories": ["repo-one", "repo-one"]},
        {"repositories": ["repo-one", ""]},
        {"repositories": "repo-one,repo-two"},
        {"repository": "repo-one", "repositories": ["repo-one", "repo-two"]},
    ],
)
def test_plural_target_set_requires_unique_complete_unambiguous_paths(
    tmp_path: Path, target: dict[str, object]
) -> None:
    import yaml

    path = tmp_path / "run.yml"
    path.write_text(
        yaml.safe_dump(
            {"version": 2, "source": {"kind": "file-plan", "path": "plan.yml"}, "target": target}
        )
    )
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(path)


@pytest.mark.parametrize("source", ("file", "catalog"))
def test_plural_normalization_keeps_all_targets_without_a_primary_repository(
    tmp_path: Path, source: str
) -> None:
    import yaml

    repositories = []
    for name in ("application-one", "application-two"):
        root = tmp_path / name
        root.mkdir()
        repositories.append(RepositoryIdentity.model_validate({"path": root}))
    package = tmp_path / "plans/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n")
    plan = package / "plan.yml" if source == "catalog" else tmp_path / "plan.yml"
    plan.write_text("version: 1\ngoons: []\n")
    path = tmp_path / "run.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "catalog-plan", "name": "general/check"}
                if source == "catalog"
                else {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repositories": [item.path.name for item in repositories]},
            }
        )
    )
    snapshot = load_run_configuration(path)
    normalized = normalize_run_configuration(
        snapshot, repositories, plan_templates=PlanTemplateStore(tmp_path / "plans")
    )
    assert normalized.repositories == tuple(repositories)
    assert normalized.plan_path == plan
    assert normalized.resolution_repository is None
    assert snapshot.selection.target.model_dump() == {
        "repositories": tuple(item.path.name for item in repositories)
    }
    with pytest.raises(FleetPlanInvalid, match="single repository context"):
        _ = normalized.repository
    for incomplete in (repositories[:1], [*repositories, repositories[1]]):
        with pytest.raises(FleetPlanInvalid, match="every selected path"):
            normalize_run_configuration(snapshot, incomplete)
    excluded = ExcludedRepositoryCatalogEntry(
        scope=FleetScope.EXCLUDED,
        repository=repositories[1],
        exclusion_reason="explicit exclusion",
        excluded_at=utc_now(),
    )
    with pytest.raises(FleetPlanInvalid, match="every selected path"):
        normalize_run_configuration(snapshot, [repositories[0], excluded])


@pytest.mark.parametrize("single_workspace", ("repository-goon", "agent", "repair"))
def test_plural_selection_refuses_single_workspace_contracts_before_source_reads(
    tmp_path: Path, single_workspace: str
) -> None:
    import yaml

    payload: dict[str, object] = {
        "version": 2,
        "source": {"kind": "file-plan", "path": "unavailable.yml"},
        "target": {"repositories": ["repo-one", "repo-two"]},
    }
    if single_workspace == "repository-goon":
        payload["source"] = {"kind": "repository-goon"}
    else:
        payload["agent"] = {
            "model_profile": "fixture",
            "acceptance": "review",
            "repository_root": "/unavailable",
            "skill_root": ".",
        }
        if single_workspace == "repair":
            payload["repair"] = {
                "review_passes": 1,
                "write_roots": ["src"],
                "timeout_seconds": 10,
                "step_timeout_seconds": 5,
            }
    path = tmp_path / "run.yml"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(FleetPlanInvalid, match="selection fields"):
        load_run_configuration(path)


def test_singular_target_serialization_keeps_existing_contract() -> None:
    selected = RunConfiguration.model_validate(
        {
            "version": 2,
            "source": {"kind": "file-plan", "path": "plan.yml"},
            "target": {"repository": "repo-one"},
        }
    )
    assert selected.target.model_dump() == {"repository": "repo-one"}


@pytest.mark.parametrize("selected", ["../outside.yml", "/tmp/elsewhere.yml", "alias.yml"])
def test_file_plan_cannot_escape_or_alias_workflow_root(tmp_path, selected):
    outside = tmp_path.parent / "outside.yml"
    outside.write_text("version: 1\n", encoding="utf-8")
    (tmp_path / "alias.yml").symlink_to(outside)
    path, repository = _configuration(tmp_path, source=f"{{kind: file-plan, path: {selected}}}")
    with pytest.raises(FleetPlanInvalid):
        normalize_run_configuration(load_run_configuration(path), [repository])


def test_symlink_configuration_is_not_a_source_identity(tmp_path):
    path, _ = _configuration(tmp_path)
    alias = tmp_path / "alias.yml"
    alias.symlink_to(path)
    with pytest.raises(FleetPlanInvalid, match="symlink"):
        load_run_configuration(alias)


def test_catalog_source_does_not_copy_or_merge_repository_goon(tmp_path):
    path, repository = _configuration(tmp_path, source="{kind: catalog-plan, name: general/check}")
    package = tmp_path / "plans/general/check"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/check\n", encoding="utf-8")
    catalog_plan = package / "plan.yml"
    catalog_plan.write_text("version: 1\ngoons: []\n", encoding="utf-8")
    normalized = normalize_run_configuration(
        load_run_configuration(path),
        [repository],
        plan_templates=PlanTemplateStore(tmp_path / "plans"),
    )
    assert normalized.plan_path == catalog_plan
    assert not (tmp_path / "plan.yml").exists()
    assert not (repository.path / "goon.yml").exists()


def test_repository_source_selects_only_registered_root_goon(tmp_path):
    path, repository = _configuration(tmp_path, source="{kind: repository-goon}")
    with pytest.raises(FleetPlanInvalid):
        normalize_run_configuration(load_run_configuration(path), [repository])
    goon = repository.path / "goon.yml"
    goon.write_text("version: 1\nsteps: []\n", encoding="utf-8")
    normalized = normalize_run_configuration(load_run_configuration(path), [repository])
    assert normalized.plan_path == goon


def test_agent_selection_and_repair_normalize_only_existing_invocation_meanings(tmp_path):
    path, repository = _configuration(tmp_path)
    (tmp_path / "plan.yml").write_text("version: 1\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "inputs: {repo: 'local:repository', goal: inspect}\n"
            "agent:\n  implementation: pi\n  profile: isolated-managed\n"
            f"  repository_root: {tmp_path}\n  skill_root: .\n  acceptance: agentic-review\n"
            "repair:\n  review_passes: 5\n  write_roots: [src, tests]\n"
            "  timeout_seconds: 1800\n  step_timeout_seconds: 300\n"
        )
    normalized = normalize_run_configuration(load_run_configuration(path), [repository])
    selection = normalized.snapshot.selection
    assert normalized.inputs["acceptance"] == "agentic-review"
    assert selection.repair.budget().max_attempts == 5
    assert selection.repair.write_roots == ("src", "tests")
    assert normalized.snapshot.agent_repository_root() == tmp_path
    assert normalized.snapshot.agent_skill_root() == tmp_path


@pytest.mark.parametrize(
    "agent_extra",
    ["model_profile: review", "implementation: opencode", "contextWindow: 100000"],
)
def test_native_model_and_duplicate_native_selection_are_refused(tmp_path, agent_extra):
    path, _ = _configuration(tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "agent:\n  implementation: pi\n  profile: isolated-managed\n"
            f"  repository_root: {tmp_path}\n  skill_root: .\n  acceptance: review\n"
            f"  {agent_extra}\n"
        )
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(path)


def test_run_configuration_bytes_are_required_lock_identity_and_admission_custody(tmp_path):
    path, repository = _configuration(tmp_path)
    plan_path, resolver = _plan(tmp_path)
    snapshot = load_run_configuration(path)

    class Runs:
        target = None

        def require_plan_ingress_preparation(self, fleet_run_id):
            assert fleet_run_id == "selected-run"

        def start_prepared_plan(self, target, **kwargs):
            self.target = target
            return object()

    runs = Runs()
    service = FleetPlanService(
        resolver,
        cast(object, runs),
        cast(object, object()),
        cast(object, object()),
        cast(object, object()),
    )
    lock = service.lock(
        plan_path,
        inputs=snapshot.inputs,
        output=snapshot.lock_path,
        external_artifacts=snapshot.external_artifacts(),
    )
    service.run(
        plan_path,
        inputs=snapshot.inputs,
        repository_ids={repository.repository_id},
        fleet_run_id="selected-run",
        lock_path=snapshot.lock_path,
        caller_input_file=snapshot.authority(),
        external_artifacts=snapshot.external_artifacts(),
    )
    target = runs.target
    assert target.caller_input_file == snapshot.authority()
    assert (
        target.execution_digest
        == build_execution_lock(target.plan, external_identities=target.lock_artifacts).digest
    )
    assert lock.external[0].path == "run.yml"
    assert lock.digest == target.execution_digest
    assert target.lock_artifacts == lock.external
    # Even an otherwise non-semantic byte change requires the explicit new lock.
    path.write_text(path.read_text() + "# changed operator selection\n", encoding="utf-8")
    changed = load_run_configuration(path)
    runs.target = None
    with pytest.raises(FleetPlanInvalid, match="external artifact identity is stale"):
        service.run(
            plan_path,
            inputs=changed.inputs,
            repository_ids={repository.repository_id},
            fleet_run_id="selected-run",
            lock_path=changed.lock_path,
            caller_input_file=changed.authority(),
            external_artifacts=changed.external_artifacts(),
        )
    assert runs.target is None


def test_unknown_and_wrong_typed_values_remain_canonical_binder_refusals(tmp_path):
    path, _ = _configuration(tmp_path)
    plan_path, resolver = _plan(tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("inputs: {unknown: true}\n")
    selected = load_run_configuration(path)
    with pytest.raises(FleetPlanInvalid, match="unknown"):
        resolver.resolve(plan_path, inputs=selected.inputs)
    path.write_text(path.read_text().replace("unknown: true", "count: invalid"), encoding="utf-8")
    selected = load_run_configuration(path)
    with pytest.raises(FleetPlanInvalid, match="integer"):
        resolver.resolve(plan_path, inputs=selected.inputs)


def test_schema_diagnostics_publish_only_safe_field_locations(tmp_path):
    from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic

    path = tmp_path / "run.yml"
    path.write_text(
        "version: 2\nsource: {kind: catalog-plan, name: python/quality}\n"
        "target: {repository: []}\nsecret-sentinel: private-value\n"
    )
    with pytest.raises(FleetPlanInvalid) as error:
        load_run_configuration(path)
    report = safe_plan_diagnostic(error.value)
    assert report["code"] == "CONFIG_SCHEMA_INVALID"
    assert report["fields"] == ["target.repository"]
    assert "secret-sentinel" not in str(report)
    assert "private-value" not in str(report)


@pytest.mark.parametrize(
    "field,value",
    [
        ("git", "https://user:PRIVATE_TOKEN@example.test/private.git"),
        ("ref", "--PRIVATE_REF"),
        ("plan", "../PRIVATE_PLAN.yml"),
    ],
)
def test_git_selection_diagnostics_name_safe_fields_without_echoing_values(tmp_path, field, value):
    import yaml

    from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic

    path, _ = _configuration(tmp_path)
    declaration = yaml.safe_load(path.read_text())
    declaration["source"] = {
        "kind": "git-plan",
        "git": "https://example.test/workflows.git",
        "ref": "main",
        "path": ".",
        "plan": "plan.yml",
    }
    declaration["source"][field] = value
    path.write_text(yaml.safe_dump(declaration))
    with pytest.raises(FleetPlanInvalid) as error:
        load_run_configuration(path)
    report = safe_plan_diagnostic(error.value)
    assert report["fields"] == ["source." + field]
    assert "PRIVATE_" not in str(report)


def test_configuration_change_during_lock_resolution_cannot_replace_lock(tmp_path, monkeypatch):
    path, _ = _configuration(tmp_path)
    plan_path, resolver = _plan(tmp_path)
    snapshot = load_run_configuration(path)
    snapshot.lock_path.write_text("retained-lock", encoding="utf-8")
    original = resolver.resolve_source_graph

    def mutate_after_resolution(*args, **kwargs):
        plan = original(*args, **kwargs)
        path.write_text(path.read_text() + "# changed during resolution\n", encoding="utf-8")
        return plan

    monkeypatch.setattr(resolver, "resolve_source_graph", mutate_after_resolution)
    service = FleetPlanService(resolver, None, None, None, None)
    with pytest.raises(FleetPlanInvalid, match="changed"):
        service.lock(
            plan_path,
            output=snapshot.lock_path,
            external_artifacts=snapshot.external_artifacts(),
            caller_input_file=snapshot.authority(),
        )
    assert snapshot.lock_path.read_text() == "retained-lock"


def test_file_plan_cannot_reinterpret_a_goon_source(tmp_path):
    path, repository = _configuration(tmp_path, source="{kind: file-plan, path: goon.yml}")
    (tmp_path / "goon.yml").write_text("version: 1\nsteps: [{run: 'true'}]\n")
    with pytest.raises(FleetPlanInvalid, match="file-plan selects a Plan"):
        normalize_run_configuration(load_run_configuration(path), [repository])


def test_repository_goon_selection_keeps_original_source_and_run_provenance(tmp_path):
    from oh_goon.core.models.goon_plan_source import TeamGoonSource
    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.core.vocabulary import FleetRunSource

    path, repository = _configuration(tmp_path, source="{kind: repository-goon}")
    _, resolver = _plan(tmp_path)
    goon = repository.path / "goon.yml"
    goon.write_text("version: 1\ngoons: [general/check]\n")
    snapshot = load_run_configuration(path)
    observed = []

    class Runs:
        def require_plan_ingress_preparation(self, fleet_run_id):
            pass

        def start_prepared_plan(self, target, **kwargs):
            observed.append((target, kwargs))
            return object()

    service = FleetPlanService(resolver, Runs(), None, None, None)
    service.lock(
        goon,
        output=snapshot.lock_path,
        external_artifacts=snapshot.external_artifacts(),
        caller_input_file=snapshot.authority(),
    )
    service.run(
        goon,
        repository_ids={repository.repository_id},
        lock_path=snapshot.lock_path,
        external_artifacts=snapshot.external_artifacts(),
        caller_input_file=snapshot.authority(),
        repository_goon=True,
    )
    target, arguments = observed[0]
    assert isinstance(target.source, TeamGoonSource)
    assert arguments["source"] is FleetRunSource.CICD
    frozen = target.model_dump_json()
    goon.write_text("not: [valid-yaml")
    assert FleetPlanTarget.model_validate_json(frozen) == target
    observed.clear()
    with pytest.raises(FleetPlanInvalid, match="exact selected repository root"):
        service.run(
            goon,
            repository_ids={"other-repository"},
            lock_path=snapshot.lock_path,
            external_artifacts=snapshot.external_artifacts(),
            caller_input_file=snapshot.authority(),
            repository_goon=True,
        )
    assert observed == []


def test_lock_and_run_reuse_one_source_graph_with_explicit_repository_context(
    tmp_path, monkeypatch
):
    path, repository = _configuration(tmp_path)
    plan_path, resolver = _plan(tmp_path)
    plan_path.write_text(
        "version: 1\ngoons:\n- import: general/check\n"
        "  with: {count: {from: {text: {path: COUNT}}}}\n"
    )
    (repository.path / "COUNT").write_text("11\n")
    graph = resolver.analyze_source(plan_path)
    snapshot = load_run_configuration(path)
    observed = []

    class Runs:
        def require_plan_ingress_preparation(self, fleet_run_id):
            pass

        def start_prepared_plan(self, target, **kwargs):
            observed.append(target)
            return object()

    def no_reparse(*args, **kwargs):
        pytest.fail("selected source graph was reacquired")

    monkeypatch.setattr(resolver, "analyze_source", no_reparse)
    service = FleetPlanService(resolver, Runs(), None, None, None)
    lock = service.lock(
        plan_path,
        source_graph=graph,
        repository=repository.path,
        output=snapshot.lock_path,
        external_artifacts=snapshot.external_artifacts(),
        caller_input_file=snapshot.authority(),
    )
    service.run(
        plan_path,
        source_graph=graph,
        repository=repository.path,
        repository_ids={repository.repository_id},
        lock_path=snapshot.lock_path,
        external_artifacts=snapshot.external_artifacts(),
        caller_input_file=snapshot.authority(),
    )
    assert observed[0].plan.steps[0].argv == ("echo", "11")
    assert observed[0].execution_digest == lock.digest
    assert observed[0].lock_artifacts == lock.external


def test_captured_source_graph_cannot_select_another_path(tmp_path):
    _, repository = _configuration(tmp_path)
    plan_path, resolver = _plan(tmp_path)
    graph = resolver.analyze_source(plan_path)
    other = tmp_path / "other.yml"
    other.write_bytes(plan_path.read_bytes())
    service = FleetPlanService(resolver, None, None, None, None)
    with pytest.raises(FleetPlanInvalid, match="different selected source"):
        service.lock(other, source_graph=graph, repository=repository.path)
    assert not (tmp_path / "goon.lock").exists()


def test_agent_capture_consumes_the_original_graph_without_source_rediscovery(
    tmp_path, monkeypatch
):
    from tests.factories.agent_review import agent_review_case

    resolver, path, _, options = agent_review_case(tmp_path)
    graph = resolver.analyze_source(path)
    monkeypatch.setattr(
        resolver, "analyze_source", lambda *a, **kw: pytest.fail("agent graph was reacquired")
    )
    _, deliveries, source_sha = resolver.resolve_frozen_agents(path, source_graph=graph, **options)
    assert source_sha == graph.root.source_sha256
    assert all(
        packet.source.plan_graph_identity == graph.immutable_source_closure_identity()
        for packet in deliveries.values()
    )
