from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_supply_contract import (
    check_project_supply_contract,
)


def _write_run_log_protocol_fixture(root: Path, *, progress_binding: str) -> None:
    script = root / "scripts/run_logged.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "printf 'Working directory: <repo>\\n'",
                "printf '<state>/run-logs <home> <tmp>\\n'",
                progress_binding,
                "",
            )
        ),
        encoding="utf-8",
    )


def test_run_log_architecture_accepts_semantic_protocol_without_shell_choreography(
    tmp_path: Path,
) -> None:
    _write_run_log_protocol_fixture(
        tmp_path,
        progress_binding='export OH_GOON_PROGRESS_FD="${OH_GOON_PRESENTATION_FD:-9}"',
    )

    assert check_project_supply_contract(tmp_path) == []


def test_run_log_architecture_still_requires_progress_protocol_identity(tmp_path: Path) -> None:
    _write_run_log_protocol_fixture(tmp_path, progress_binding='export PRESENTATION_FD="9"')

    violations = check_project_supply_contract(tmp_path)

    assert len(violations) == 1
    assert "OH_GOON_PROGRESS_FD" in violations[0].message
