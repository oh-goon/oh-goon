from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation


def _check_multitab_schedule_uniqueness(root: Path) -> list[Violation]:
    """Protect the durable database uniqueness boundary for Schedule starts."""
    database_schema = root / "backend" / "src" / "oh_goon" / "runtime" / "_database_schema.py"
    if not database_schema.is_file():
        return [
            Violation(
                database_schema,
                "durable Schedule uniqueness schema owner must exist",
            )
        ]
    if (
        "CREATE UNIQUE INDEX IF NOT EXISTS schedule_one_active_execution"
        in database_schema.read_text(encoding="utf-8")
    ):
        return []
    return [
        Violation(
            database_schema,
            "multi-tab Schedule starts must remain serialized by the durable one-active-execution constraint",
        )
    ]
