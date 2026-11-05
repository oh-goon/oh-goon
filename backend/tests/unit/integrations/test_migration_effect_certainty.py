import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import (
    ExternalEffectOutcomeUnknown,
    MigrationPreconditionFailed,
    StateConflict,
)
from oh_goon.core.models.target import MigrationTarget
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations.migration import MigrationRunner
from oh_goon.integrations.migration_git import publish_migration_commit
from tests.factories.capabilities import capability_request, execute_capability_runner


class _Git:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, *argv: str, **_kwargs) -> CommandResult:
        self.calls.append(argv)
        if argv == ("rev-parse", "HEAD"):
            return CommandResult(returncode=1, stdout="", stderr="cannot observe HEAD", argv=argv)
        return CommandResult(returncode=0, stdout="ok", stderr="", argv=argv)


def test_migration_commit_success_without_observable_head_is_outcome_unknown(
    tmp_path: Path,
) -> None:
    git = _Git()

    with pytest.raises(ExternalEffectOutcomeUnknown, match="repository mutation outcome"):
        publish_migration_commit(
            git,  # type: ignore[arg-type]
            tmp_path,
            operation_id="operation-12345678",
            changed=["pyproject.toml"],
        )

    assert ("commit", "-m", "chore: adopt uv") in git.calls
    assert git.calls[-1] == ("rev-parse", "HEAD")


@pytest.mark.parametrize("unavailable_at", [None, 1, 2])
def test_migration_requires_observed_clean_start_and_available_publication_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unavailable_at: int | None
) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='candidate'\n", encoding="utf-8")
    observations = 0
    package_commands: list[tuple[str, ...]] = []
    mutations: list[tuple[str, ...]] = []

    def native_git(_executable: str, *argv: str, **_kwargs: object) -> CommandResult:
        nonlocal observations
        if argv[0] == "status":
            observations += 1
            if observations == unavailable_at:
                return CommandResult(128, "", "Git status failed")
            changed = "? uv.lock\0" if observations > 1 else ""
            return CommandResult(0, "# branch.oid (initial)\0# branch.head main\0" + changed, "")
        mutations.append(argv)
        return CommandResult(0, "a" * 40 if argv == ("rev-parse", "HEAD") else "", "")

    def native_uv(*argv: str, **_kwargs: object) -> CommandResult:
        package_commands.append(argv)
        (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        return CommandResult(0, "", "")

    monkeypatch.setattr("oh_goon.integrations.migration_execution.run_command", native_uv)
    runner = MigrationRunner(GitService(command_runner=native_git))
    request = capability_request(
        tmp_path, OperationAction.ADOPT_UV, MigrationTarget(kind="migration")
    )

    if unavailable_at is None:
        result = execute_capability_runner(runner, request)
        assert result.report.outcome.state == "successful"
        assert mutations and package_commands
    else:
        expected_error = MigrationPreconditionFailed if unavailable_at == 1 else StateConflict
        with pytest.raises(expected_error):
            execute_capability_runner(runner, request)
        assert not mutations
        assert bool(package_commands) is (unavailable_at > 1)
