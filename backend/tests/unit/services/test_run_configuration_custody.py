"""Canonical selection capture remains bounded and never rebinds at a freshness fence."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.run_configuration import load_run_configuration


def _file(tmp_path: Path, inputs: str = "inputs: {coverage_threshold: 85}\n") -> Path:
    path = tmp_path / "run.yml"
    path.write_text(
        "version: 2\nsource: {kind: catalog-plan, name: python/quality}\n"
        "target: {repository: .}\n" + inputs,
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "inputs",
    [
        "inputs: {x: 1.0}\n",
        "inputs: {x: 2026-10-11}\n",
        "inputs: {bad.name: 1}\n",
        "inputs: {x: !custom value}\n",
        "inputs: {<<: {x: 1}}\n",
        "inputs: null\n",
    ],
)
def test_invalid_scalar_shapes_do_not_reach_value_resolution(tmp_path, inputs):
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(_file(tmp_path, inputs))


def test_bad_encoding_oversized_and_too_many_names_are_bounded(tmp_path):
    path = _file(tmp_path)
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(path)
    path.write_bytes(b" " * 65537)
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(path)
    _file(tmp_path, "inputs:\n" + "".join(f"  k{i}: {i}\n" for i in range(65)))
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(path)


def test_same_bytes_replacement_requires_new_file_custody(tmp_path):
    source = _file(tmp_path)
    snapshot = load_run_configuration(source)
    other = tmp_path / "replacement.yml"
    other.write_bytes(source.read_bytes())
    other.replace(source)
    with pytest.raises(FleetPlanInvalid, match="replaced after snapshot"):
        snapshot.require_fresh()
    assert snapshot.inputs == {"coverage_threshold": 85}


def test_removed_source_or_swapped_link_is_stale(tmp_path):
    source = _file(tmp_path)
    snapshot = load_run_configuration(source)
    source.unlink()
    with pytest.raises(FleetPlanInvalid, match="not a readable regular file"):
        snapshot.require_fresh()
    source.symlink_to(tmp_path / "secret.yml")
    with pytest.raises(FleetPlanInvalid, match="symlink"):
        snapshot.require_fresh()


def test_freshness_never_reparses_yaml_or_reselects_values(tmp_path, monkeypatch):
    source = _file(tmp_path)
    snapshot = load_run_configuration(source)
    from oh_goon.services import run_configuration

    def forbidden(*args, **kwargs):
        raise AssertionError("freshness cannot select a new value")

    monkeypatch.setattr(run_configuration.yaml, "load", forbidden)
    snapshot.require_fresh()
    assert snapshot.inputs == {"coverage_threshold": 85}


def test_mutation_during_freshness_check_is_refused(tmp_path, monkeypatch):
    source = _file(tmp_path)
    snapshot = load_run_configuration(source)
    from oh_goon.services import fleet_caller_file_freshness

    original = fleet_caller_file_freshness.read_caller_file_bytes

    def change_after_snapshot(path, *, expected):
        data = original(path, expected=expected)
        _file(tmp_path, "inputs: {coverage_threshold: 87}\n")
        return data

    monkeypatch.setattr(
        fleet_caller_file_freshness, "read_caller_file_bytes", change_after_snapshot
    )
    with pytest.raises(FleetPlanInvalid, match="changed after"):
        snapshot.require_fresh()


def test_extreme_yaml_nesting_is_a_typed_refusal(tmp_path):
    nested = "[" * 2000 + "value" + "]" * 2000
    source = _file(tmp_path, f"inputs: {{value: {nested}}}\n")
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(source)


def test_replaced_workflow_root_symlink_is_stale_even_with_same_file_inode(tmp_path):
    root = tmp_path / "workflow"
    root.mkdir()
    snapshot = load_run_configuration(_file(root))
    retained = tmp_path / "renamed-workflow"
    root.rename(retained)
    root.symlink_to(retained, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="root cannot contain symlink"):
        snapshot.require_fresh()
