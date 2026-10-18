from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.vocabulary import MigrationSource
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.migration_execution import execute_migration_plan
from oh_goon.integrations.migration_plan import automatic_migration_plan


def test_m93_automatic_adopt_uv_has_one_native_decision_per_boundary() -> None:
    pyproject = automatic_migration_plan(
        MigrationSource.PYPROJECT,
        has_compiled_requirements=False,
    )
    assert pyproject.steps == (
        ("uv", "lock"),
        ("uv", "sync", "--frozen"),
    )
    assert pyproject.validation_steps == (
        "uv lock",
        "uv sync --frozen",
        "git diff --cached --check",
    )

    pip_tools = automatic_migration_plan(
        MigrationSource.PIP_TOOLS,
        has_compiled_requirements=True,
    )
    assert pip_tools.steps == (
        (
            "uv",
            "init",
            "--bare",
            "--no-workspace",
            "--no-pin-python",
            "--author-from",
            "none",
        ),
        ("uv", "add", "--no-sync", "-r", "requirements.in", "-c", "requirements.txt"),
        ("uv", "sync", "--frozen"),
    )
    assert pip_tools.validation_steps == (
        "uv init --bare --no-workspace --no-pin-python --author-from none",
        "uv add --no-sync -r requirements.in -c requirements.txt",
        "uv sync --frozen",
        "git diff --cached --check",
    )

    for plan in (pyproject, pip_tools):
        assert ("uv", "lock", "--check") not in plan.steps
        assert sum(step[:2] == ("uv", "sync") for step in plan.steps) == 1
        assert all("uv lock --check" not in step for step in plan.validation_steps)
        assert all("uv sync --locked" not in step for step in plan.validation_steps)


def test_m93_pip_tools_without_compiled_requirements_keeps_one_add_decision() -> None:
    plan = automatic_migration_plan(
        MigrationSource.PIP_TOOLS,
        has_compiled_requirements=False,
    )

    assert plan.steps[1] == ("uv", "add", "--no-sync", "-r", "requirements.in")
    assert plan.validation_steps[1] == "uv add --no-sync -r requirements.in"
    assert sum(step[:2] == ("uv", "sync") for step in plan.steps) == 1


@pytest.mark.parametrize(
    ("source", "compiled"),
    (
        (MigrationSource.PYPROJECT, False),
        (MigrationSource.PIP_TOOLS, False),
        (MigrationSource.PIP_TOOLS, True),
    ),
)
def test_m93_execution_never_invokes_lock_check_even_when_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: MigrationSource,
    compiled: bool,
) -> None:
    repository = tmp_path / source.value
    repository.mkdir()
    calls: list[tuple[str, ...]] = []

    def fake_run_command(*argv: str, **_kwargs: object) -> CommandResult:
        calls.append(argv)
        if argv == ("uv", "lock", "--check"):
            pytest.fail("automatic Adopt-UV must not re-decide freshness with uv lock --check")
        if argv == ("uv", "sync", "--frozen"):
            (repository / ".venv").mkdir(exist_ok=True)
        return CommandResult(returncode=0, stdout="ok", stderr="", argv=argv)

    monkeypatch.setattr("oh_goon.integrations.migration_execution.run_command", fake_run_command)
    plan = automatic_migration_plan(source, has_compiled_requirements=compiled)

    result = execute_migration_plan(repository, plan)

    assert result.succeeded is True
    assert tuple(calls) == plan.steps
    assert ("uv", "lock", "--check") not in calls
    assert calls.count(("uv", "sync", "--frozen")) == 1
    assert not (repository / ".venv").exists()
