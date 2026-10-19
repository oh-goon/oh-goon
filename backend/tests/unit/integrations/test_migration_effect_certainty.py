from pathlib import Path

import pytest

from oh_goon.core.errors import ExternalEffectOutcomeUnknown
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.migration_git import publish_migration_commit


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
