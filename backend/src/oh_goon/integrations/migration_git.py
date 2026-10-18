from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import ExternalEffectOutcomeUnknown
from oh_goon.core.product_identity import opaque_identity_token
from oh_goon.engine.git import GitService

Cancel = Callable[[], bool] | None


@dataclass(frozen=True, slots=True)
class MigrationCommit:
    branch: str
    commit: str | None
    output: tuple[str, ...]


def publish_migration_commit(
    git: GitService,
    repository: Path,
    *,
    operation_id: str,
    changed: list[str],
    cancel_requested: Cancel = None,
) -> MigrationCommit:
    branch = f"chore/oh-goon/adopt-uv-{opaque_identity_token(operation_id)}"
    output: list[str] = []
    commands = (
        ("switch", "-c", branch),
        ("add", *changed),
        ("diff", "--cached", "--check"),
        ("commit", "-m", "chore: adopt uv"),
    )
    for argv in commands:
        result = git.run(*argv, cwd=repository, timeout=120.0, cancel_requested=cancel_requested)
        output.append(result.stdout or result.stderr)
        if result.returncode != 0:
            return MigrationCommit(branch=branch, commit=None, output=tuple(output))
    head = git.run(
        "rev-parse", "HEAD", cwd=repository, timeout=30.0, cancel_requested=cancel_requested
    )
    commit = head.stdout.strip() if head.returncode == 0 else ""
    if not commit:
        raise ExternalEffectOutcomeUnknown(
            "Migration git commit reported success but the committed HEAD identity could not be "
            "observed. Automatic replay is blocked because the repository mutation outcome is "
            "not fully identified."
        )
    return MigrationCommit(branch=branch, commit=commit, output=tuple(output))
