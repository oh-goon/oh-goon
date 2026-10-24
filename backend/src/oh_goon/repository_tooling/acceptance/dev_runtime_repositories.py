from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    prepare_interruptible_acceptance_repository,
)


def prepare_runtime_acceptance_repositories(
    temp: Path, *, interruptible_step_seconds: float
) -> tuple[Path, Path, Path, Path, Path]:
    """Materialize the repository fixtures owned by local runtime acceptance."""
    repositories = temp / "repositories"
    repository = repositories / "acceptance-repo"
    cancelled = repositories / "cancelled-repo"
    competing = repositories / "competing-repo"
    interrupted = repositories / "interrupted-repo"
    repositories.mkdir()
    prepare_acceptance_repository(repository)
    prepare_interruptible_acceptance_repository(
        cancelled, duration_seconds=interruptible_step_seconds
    )
    prepare_acceptance_repository(competing)
    prepare_interruptible_acceptance_repository(
        interrupted, duration_seconds=interruptible_step_seconds
    )
    return repositories, repository, cancelled, competing, interrupted
