from __future__ import annotations

from pathlib import Path

from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

_PLAN = """version: 1
steps:
  - id: first
    run: "echo hi"
  - id: second
    run: "echo bye"
"""


def test_resolver_carries_step_source_positions_from_the_parsed_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "goon.yml"
    path.write_text(_PLAN, encoding="utf-8")

    resolved = FleetPlanResolver().resolve(path)

    assert [
        (item.step_id, item.source, item.line, item.column) for item in resolved.step_sources
    ] == [
        ("first", "goon.yml", 3, 5),
        ("second", "goon.yml", 5, 5),
    ]


def test_source_position_changes_do_not_change_semantic_plan_identity(tmp_path: Path) -> None:
    path = tmp_path / "goon.yml"
    path.write_text(_PLAN, encoding="utf-8")
    before = FleetPlanResolver().resolve(path)

    path.write_text("# documentation only\n\n" + _PLAN, encoding="utf-8")
    after = FleetPlanResolver().resolve(path)

    assert after.plan_id == before.plan_id
    assert after.step_sources[0].line == before.step_sources[0].line + 2
