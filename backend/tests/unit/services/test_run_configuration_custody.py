"""Canonical selection capture remains bounded and never rebinds at a freshness fence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.run_configuration import (
    MAX_RUN_CONFIGURATION_HANDOFF_BYTES,
    decode_run_configuration_snapshot,
    encode_run_configuration_snapshot,
    load_run_configuration,
)


def _file(tmp_path: Path, inputs: str = "inputs: {coverage_threshold: 85}\n") -> Path:
    path = tmp_path / "run.yml"
    path.write_text(
        "version: 1\nsource: {kind: catalog-plan, name: python/quality}\n"
        "target: {repository_id: selected-repository}\n" + inputs,
        encoding="utf-8",
    )
    return path


def test_resources_are_optional_without_changing_existing_captured_authority(
    tmp_path: Path,
) -> None:
    source = _file(tmp_path)
    original = source.read_bytes()
    snapshot = load_run_configuration(source)
    assert snapshot.selection.resources is None
    assert "resources" not in snapshot.selection.model_dump()
    assert snapshot.external_artifacts() == (("run.yml", original),)

    with source.open("a", encoding="utf-8") as stream:
        stream.write("resources: {cpu_millis: 1500, memory_mib: 1024, max_processes: 96}\n")
    selected = load_run_configuration(source)
    assert selected.selection.resources is not None
    assert selected.selection.resources.cpu_millis == 1500
    assert selected.selection.resources.memory_bytes == 1024 * 1024 * 1024
    assert selected.selection.resources.max_processes == 96


@pytest.mark.parametrize(
    "resources",
    ("{}", "{cpu_millis: 99}", "{memory_mib: 63}", "{max_processes: 15}", "{unknown: 7}"),
)
def test_invalid_resource_requests_refuse_at_configuration_capture(
    tmp_path: Path, resources: str
) -> None:
    source = _file(tmp_path)
    with source.open("a", encoding="utf-8") as stream:
        stream.write(f"resources: {resources}\n")
    with pytest.raises(FleetPlanInvalid, match="selection fields"):
        load_run_configuration(source)


def test_snapshot_handoff_transports_original_values_without_source_reads_or_yaml_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.services import run_configuration

    source = _file(tmp_path)
    with source.open("a", encoding="utf-8") as stream:
        stream.write("resources: {cpu_millis: 1500}\n")
    snapshot = load_run_configuration(source)
    encoded = encode_run_configuration_snapshot(snapshot)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Captured selections cannot be read or interpreted again")

    monkeypatch.setattr(run_configuration.yaml, "load", forbidden)
    monkeypatch.setattr(run_configuration, "read_caller_file_bytes", forbidden)
    source.unlink()
    restored = decode_run_configuration_snapshot(encoded)
    assert restored.selection == snapshot.selection
    assert restored.content == snapshot.content
    assert restored.identity == snapshot.identity
    assert restored.inputs == {"coverage_threshold": 85}
    assert restored.selection.resources is not None
    assert restored.selection.resources.cpu_millis == 1500


@pytest.mark.parametrize("corruption", ("bytes", "base64", "schema", "oversized"))
def test_snapshot_handoff_refuses_corrupt_or_unbounded_transport(
    tmp_path: Path, corruption: str
) -> None:
    snapshot = load_run_configuration(_file(tmp_path))
    encoded = encode_run_configuration_snapshot(snapshot)
    assert decode_run_configuration_snapshot(encoded).identity == snapshot.identity
    payload = json.loads(encoded)
    if corruption == "bytes":
        payload["content_base64"] = "Y2hhbmdlZA=="
    elif corruption == "base64":
        payload["content_base64"] = "!"
    elif corruption == "schema":
        payload["schema_version"] = "not-supported"
    else:
        encoded = b" " * (MAX_RUN_CONFIGURATION_HANDOFF_BYTES + 1)
    if corruption != "oversized":
        encoded = json.dumps(payload).encode("utf-8")
    with pytest.raises(FleetPlanInvalid, match="(handoff|bytes)"):
        decode_run_configuration_snapshot(encoded)


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
