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
inputs:
  label: private-default
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
    assert snapshot.value_positions["inputs/label/default"].line == 8
    assert snapshot.value_positions["inputs/label/default"].column == 10


def test_value_locations_are_derived_from_the_original_source_tree(tmp_path: Path):
    path = tmp_path / "goon.yml"
    path.write_text("""version: 1
inputs:
  project: {type: string, default: .}
env:
  TOKEN: {from: host}
steps:
- run: echo ok
""")
    snapshot = read_fleet_plan_source(path, kind="goon")
    assert snapshot.value_positions["inputs/project"].line == 3
    assert snapshot.value_positions["env/TOKEN"].line == 5
