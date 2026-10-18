from __future__ import annotations

from pathlib import Path

from oh_goon.core.game_tape_reproduction_context import compare_reproduction_context
from oh_goon.core.models.repository import RepositoryIdentity
from tests.unit.core.test_game_tape_failure_history import _failed_detail


def test_reproduction_context_summary_is_backend_owned_and_complete(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    historical = _failed_detail(repository, run_id="run-historical", stderr="same\n")
    current_repository = current.game_tape.repositories[0]
    historical_repository = historical.game_tape.repositories[0]

    comparison = compare_reproduction_context(
        current_repository,
        current_repository.steps[0],
        historical_repository,
        historical_repository.steps[0],
    )

    assert comparison.summary.classification == "SAME_CONTEXT"
    assert comparison.summary.known_dimensions == 5
    assert comparison.summary.matching_dimensions == 5
    assert comparison.summary.different_dimensions == 0


def test_reproduction_context_reports_changed_and_incomplete_dimensions(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    changed = _failed_detail(
        repository,
        run_id="run-changed",
        stderr="same\n",
        command=("pytest", "-x"),
        workspace_sha256="b" * 64,
    )
    current_repository = current.game_tape.repositories[0]
    changed_repository = changed.game_tape.repositories[0]

    comparison = compare_reproduction_context(
        current_repository,
        current_repository.steps[0],
        changed_repository,
        changed_repository.steps[0],
    )
    assert comparison.summary.classification == "CHANGED_CONTEXT"
    assert comparison.summary.different_dimensions == 1

    incomplete_repository = current_repository.model_copy(
        update={
            "fingerprint": current_repository.fingerprint.model_copy(
                update={"host_toolchain_id": None}
            )
        }
    )
    incomplete = compare_reproduction_context(
        current_repository,
        current_repository.steps[0],
        incomplete_repository,
        current_repository.steps[0],
    )
    assert incomplete.summary.classification == "INCOMPLETE_CONTEXT"
    assert incomplete.summary.known_dimensions == 4
    assert incomplete.summary.matching_dimensions == 4
    assert incomplete.summary.different_dimensions == 0
