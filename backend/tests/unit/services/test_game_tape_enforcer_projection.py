from __future__ import annotations

from pathlib import Path

from tests.unit.services.test_enforcer_run_receipt import _detail


def test_game_tape_projects_enforcer_receipt_availability_from_strict_cicd_detail(
    tmp_path: Path,
) -> None:
    detail = _detail(tmp_path)
    assert detail.game_tape.enforcer_receipt_available is True
