from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.errors import MigrationSourceUnsupported
from oh_goon.core.vocabulary import MigrationSource


@dataclass(frozen=True, slots=True)
class AutomaticMigrationPlan:
    source: MigrationSource
    steps: tuple[tuple[str, ...], ...]
    allowed_changed_files: frozenset[str]
    validation_steps: tuple[str, ...]


def automatic_migration_plan(
    source: MigrationSource,
    *,
    has_compiled_requirements: bool,
) -> AutomaticMigrationPlan:
    """Return the complete command/file contract for one supported automatic migration source."""
    if source is MigrationSource.PYPROJECT:
        return AutomaticMigrationPlan(
            source=source,
            steps=(("uv", "lock"), ("uv", "sync", "--frozen")),
            allowed_changed_files=frozenset({"uv.lock"}),
            validation_steps=("uv lock", "uv sync --frozen", "git diff --cached --check"),
        )
    if source is MigrationSource.PIP_TOOLS:
        add = ("uv", "add", "--no-sync", "-r", "requirements.in")
        if has_compiled_requirements:
            add += ("-c", "requirements.txt")
        return AutomaticMigrationPlan(
            source=source,
            steps=(
                (
                    "uv",
                    "init",
                    "--bare",
                    "--no-workspace",
                    "--no-pin-python",
                    "--author-from",
                    "none",
                ),
                add,
                ("uv", "sync", "--frozen"),
            ),
            allowed_changed_files=frozenset({"pyproject.toml", "uv.lock"}),
            validation_steps=(
                "uv init --bare --no-workspace --no-pin-python --author-from none",
                " ".join(add),
                "uv sync --frozen",
                "git diff --cached --check",
            ),
        )
    raise MigrationSourceUnsupported(f"Unsupported automatic migration source: {source.value}")
