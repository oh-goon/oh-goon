from __future__ import annotations

from pathlib import Path

from oh_goon.integrations import fleet_plan_compilation
from oh_goon.integrations.fleet_plan_source_snapshot import read_fleet_plan_source


def test_source_snapshot_parses_yaml_once_and_reuses_tree_for_positions(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "goon.yml"
    path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - id: check
    run: "true"
""",
        encoding="utf-8",
    )
    calls = 0
    original = fleet_plan_compilation.yaml.SafeLoader.get_single_node

    def counted(loader):
        nonlocal calls
        calls += 1
        return original(loader)

    monkeypatch.setattr(fleet_plan_compilation.yaml.SafeLoader, "get_single_node", counted)

    snapshot = read_fleet_plan_source(path, kind="goon")

    assert calls == 1
    assert snapshot.compiled.name == "goon"
    assert snapshot.step_positions["check"].line == 5
    assert snapshot.step_positions["check"].column == 5
