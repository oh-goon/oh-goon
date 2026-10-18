from __future__ import annotations

import time
from pathlib import Path

from oh_goon.core.capability_registry import capability_for
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    MigrationOperationEvidence,
    OperationExecution,
    RecipeOperationEvidence,
)
from oh_goon.core.models.recipe import SecurityFinding, UvRecipeResult
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import OperationTarget, UvRecipeTarget
from oh_goon.core.vocabulary import (
    CommandSafety,
    OperationAction,
    UvRecipeId,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.command_center import CommandCenterService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.security import SecurityService
from tests.factories.capabilities import (
    capability_request,
    execute_capability_runner,
)
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack


class ControlPlaneRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        target: OperationTarget | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        del cancel_requested
        identity = RepositoryIdentity(name=repository.name, path=repository)
        if action is OperationAction.INIT:
            report = make_operation_report(identity, action, fleet_run_id=fleet_run_id)
            return OperationExecution(stdout="", stderr="", report=report)
        assert action is OperationAction.RECIPE
        assert isinstance(target, UvRecipeTarget)
        findings = []
        structured_output = None
        sbom_component_count = None
        verdict = Verdict.READY
        reason = ReasonCode.READY
        if target.recipe is UvRecipeId.AUDIT and repository.name == "api-b":
            findings = [
                SecurityFinding(advisory_id="GHSA-demo", package="httpx", fixed_versions=["0.28.1"])
            ]
            verdict = Verdict.MANUAL_ACTION_REQUIRED
            reason = ReasonCode.VULNERABILITIES_FOUND
        if target.recipe is UvRecipeId.SBOM:
            structured_output = {
                "bomFormat": "CycloneDX",
                "components": [{"name": repository.name}],
            }
            sbom_component_count = 1
        report = make_operation_report(
            identity,
            action,
            target=target,
            fleet_run_id=fleet_run_id,
            verdict=verdict,
            reason_code=reason,
            evidence=RecipeOperationEvidence(
                kind="RECIPE",
                recipe=UvRecipeResult(
                    recipe=target.recipe,
                    safety=CommandSafety.READ_ONLY,
                    argv=["uv", target.recipe.value.lower()],
                    exit_code=0,
                    findings=findings,
                    structured_output=structured_output,
                    sbom_component_count=sbom_component_count,
                ),
            ),
        )
        return OperationExecution(stdout="", stderr="", report=report)


def _repository(
    root: Path, name: str, pyproject: str | None = None, *, uv_lock: bool = True
) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    if pyproject is not None:
        (repository / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if uv_lock:
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return repository


def _finish(manager: FleetRunControlPlane, target: UvRecipeTarget):
    started = manager.start(OperationAction.RECIPE, target=target)
    return manager.wait_for_terminal(started.fleet_run_id, timeout=3)


def test_command_center_and_security_are_target_aware_journal_projections(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = "[project]\nname='demo'\nversion='0.1.0'\ndependencies=['httpx']\n"
    _repository(root, "api-a", project)
    _repository(root, "api-b", project)
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    repositories = RepositoryService(state, RepositoryDiscoveryService())
    fleet, manager, _, _ = fleet_stack(state, ControlPlaneRunner())

    _finish(manager, UvRecipeTarget(kind="recipe", recipe=UvRecipeId.LOCK_CHECK))
    _finish(manager, UvRecipeTarget(kind="recipe", recipe=UvRecipeId.AUDIT))
    sbom_run = _finish(manager, UvRecipeTarget(kind="recipe", recipe=UvRecipeId.SBOM))

    center = CommandCenterService(repositories, state).status()
    assert len(center.repository_ids) == 2
    assert set(center.repository_ids) == {entry.repository.repository_id for entry in repositories.included(state.read_view())}
    lock = next(row for row in center.recipes if row.spec.recipe is UvRecipeId.LOCK_CHECK)
    audit = next(row for row in center.recipes if row.spec.recipe is UvRecipeId.AUDIT)
    assert (lock.successful, lock.needs_attention, lock.not_run) == (2, 0, 0)
    assert (audit.successful, audit.needs_attention, audit.not_run) == (1, 1, 0)
    assert len(audit.attention_repository_ids) == 1

    security = SecurityService(repositories, state).summary()
    assert security.repository_ids == center.repository_ids
    assert security.repositories == len(security.repository_ids)
    assert security.audited == 2
    assert security.clean == 1
    assert security.affected_repositories == 1
    assert len(security.findings) == 1
    assert security.findings[0].advisory_id == "GHSA-demo"
    assert security.findings[0].fixed_versions == ("0.28.1",)
    assert [repo.name for repo in security.findings[0].repositories] == ["api-b"]

    bundle = SecurityService(repositories, state).sbom_bundle(sbom_run.run.fleet_run_id)
    assert len(bundle.documents) == 2
    assert {document.repository.name for document in bundle.documents} == {"api-a", "api-b"}
    assert all(document.component_count == 1 for document in bundle.documents)
    assert all(document.document["bomFormat"] == "CycloneDX" for document in bundle.documents)
    manager.shutdown()


def test_adopt_uv_preview_expands_beyond_normal_pyproject_discovery_safely(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _repository(root, "already", "[project]\nname='already'\nversion='0.1'\n", uv_lock=True)
    _repository(root, "pep621", "[project]\nname='pep621'\nversion='0.1'\n", uv_lock=False)
    _repository(root, "poetry", "[tool.poetry]\nname='poetry'\nversion='0.1'\n", uv_lock=False)
    piptools = _repository(root, "piptools", None, uv_lock=False)
    (piptools / "requirements.in").write_text("httpx\n", encoding="utf-8")
    (piptools / "requirements.txt").write_text("httpx==0.28.0\n", encoding="utf-8")

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    migration = MigrationService(state, RepositoryDiscoveryService())
    preview = migration.preview()

    assert preview.discovered == 4
    assert preview.already_uv == 1
    assert preview.safe_automatic == 2
    assert preview.review_required == 1
    assert preview.unsupported == 0
    safe_names = {entry.repository.name for entry in migration.safe_targets(state.read_view())}
    assert safe_names == {"pep621", "piptools"}


def test_recipe_target_never_uses_shell_string_parsing() -> None:
    target = UvRecipeTarget(
        kind="recipe", recipe=UvRecipeId.RUN_TASK, task="pytest", args=["-q", "tests/unit"]
    )
    assert target.task == "pytest"
    assert target.args == ("-q", "tests/unit")


def test_adopt_uv_runner_creates_local_review_commit_without_push(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    from oh_goon.core.models.target import MigrationTarget
    from oh_goon.engine.command import CommandResult
    from oh_goon.engine.command import run_command as real_run_command
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.migration import MigrationRunner

    repository = tmp_path / "pep621"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    (repository / "pyproject.toml").write_text(
        "[project]\nname='pep621'\nversion='0.1.0'\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "pyproject.toml"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"], cwd=repository, check=True, capture_output=True
    )

    def fake_command(*argv: str, **kwargs):
        if argv[0] != "uv":
            return real_run_command(*argv, **kwargs)
        if argv[:2] == ("uv", "lock") and "--check" not in argv:
            (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        if argv[:2] == ("uv", "sync"):
            (repository / ".venv").mkdir(exist_ok=True)
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.migration_execution.run_command", fake_command)
    target = MigrationTarget(
        kind="migration",
    )
    request = capability_request(repository, OperationAction.ADOPT_UV, target)
    execution = execute_capability_runner(MigrationRunner(GitService()), request)

    assert execution.report.outcome.reason_code is ReasonCode.MIGRATION_APPLIED
    assert isinstance(execution.report.evidence, MigrationOperationEvidence)
    migration = execution.report.evidence.migration
    assert migration.branch is not None
    assert migration.branch.startswith("chore/oh-goon/adopt-uv-")
    assert migration.commit
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout
    assert status == ""
    assert not (repository / ".venv").exists()
    remotes = subprocess.run(
        ["git", "remote"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout
    assert remotes == ""


def test_preview_uv_recipes_enable_only_their_required_feature_gate(tmp_path: Path) -> None:
    from oh_goon.integrations.recipe_command import prepare_recipe_command

    repository = tmp_path / "repo"
    repository.mkdir()
    expectations = {
        UvRecipeId.FORMAT_CHECK: ("uv", "--preview-features", "format-command", "format"),
        UvRecipeId.TYPE_CHECK: ("uv", "--preview-features", "check-command", "check"),
        UvRecipeId.SBOM: ("uv", "--preview-features", "sbom-export", "export"),
        UvRecipeId.AUDIT: ("uv", "--preview", "audit", "--frozen"),
    }
    for recipe, prefix in expectations.items():
        target = UvRecipeTarget(kind="recipe", recipe=recipe)
        with prepare_recipe_command(
            target, capability_for(OperationAction.RECIPE, target)
        ) as prepared:
            assert prepared.argv[: len(prefix)] == prefix
            assert prepared.temporary_output is None

    target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.TYPE_CHECK)
    with prepare_recipe_command(target, capability_for(OperationAction.RECIPE, target)) as prepared:
        assert "--isolated" in prepared.argv
        assert "--frozen" in prepared.argv
