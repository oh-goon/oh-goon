from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.vocabulary import OperationAction
from oh_goon.services.capability_developer import (
    CapabilityScaffoldRequest,
    describe_capability,
    scaffold_capability,
)


def test_capability_show_traces_distributed_owners_without_colocation() -> None:
    report = describe_capability("update")
    assert "capability_engine_definitions.py" in report
    assert "engine/runner.py" in report
    assert "application.py" in report
    assert "_application_foundation.py" in report
    assert "pass-through adapter" not in report
    assert "ExecutionSession owner transaction" in report
    assert "RepositoryEffectAttemptStore" in report


def test_capability_developer_ownership_covers_every_runner_family() -> None:
    import oh_goon.services.capability_developer as module

    assert set(module._DEFINITION_OWNERS) == set(CapabilityRunnerKind)
    assert set(module._IMPLEMENTATION_OWNERS) == set(CapabilityRunnerKind)


def test_fleet_plan_capability_show_resolves_its_real_owners() -> None:
    report = describe_capability("fleet-plan-execute")
    assert "capability_plan_definitions.py" in report
    assert "integrations/fleet_plans.py" in report


def test_capability_scaffold_generates_golden_path_packet(tmp_path: Path) -> None:
    packet = scaffold_capability(
        CapabilityScaffoldRequest(
            capability_id="hello-world",
            action=OperationAction.STATUS,
            runner_kind=CapabilityRunnerKind.ENGINE,
            label="Hello world",
            description="Demonstrate the capability golden path.",
            target_kind=CapabilityTargetKind.NONE,
        )
    )

    assert "CapabilityDefinition(" in packet.definition_snippet
    assert "CapabilityRunnerKind.ENGINE" in packet.definition_snippet
    assert "Do not add capability-specific branches to FleetService" in packet.render()
    written = packet.write(tmp_path / "scaffold")
    assert all(path.exists() for path in written)
    assert "capability_by_id" in (tmp_path / "scaffold" / "test_capability.py").read_text()


def test_capability_scaffold_refuses_to_overwrite_existing_packet(tmp_path: Path) -> None:
    packet = scaffold_capability(
        CapabilityScaffoldRequest(
            capability_id="hello-world",
            action=OperationAction.STATUS,
            runner_kind=CapabilityRunnerKind.ENGINE,
            label="Hello world",
            description="Demonstrate the capability golden path.",
            target_kind=CapabilityTargetKind.NONE,
        )
    )
    output = tmp_path / "scaffold"
    packet.write(output)
    definition = output / "capability_definition.py"
    definition.write_text("# edited by contributor\n", encoding="utf-8")

    with pytest.raises(StateConflict, match="already exists"):
        packet.write(output)

    assert definition.read_text(encoding="utf-8") == "# edited by contributor\n"
    assert (output / "test_capability.py").is_file()
    assert (output / "IMPLEMENTATION.md").is_file()


def test_capability_scaffold_staging_failure_publishes_nothing(tmp_path: Path, monkeypatch) -> None:
    packet = scaffold_capability(
        CapabilityScaffoldRequest(
            capability_id="hello-world",
            action=OperationAction.STATUS,
            runner_kind=CapabilityRunnerKind.ENGINE,
            label="Hello world",
            description="Demonstrate the capability golden path.",
            target_kind=CapabilityTargetKind.NONE,
        )
    )
    import oh_goon.services.capability_developer as module

    real_replace = module.replace_prepared_file
    calls = 0

    def fail_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected staging failure")
        return real_replace(source, target)

    monkeypatch.setattr(module, "replace_prepared_file", fail_second_replace)
    output = tmp_path / "scaffold"

    with pytest.raises(OSError, match="injected staging failure"):
        packet.write(output)

    assert not output.exists()
    assert not tuple(tmp_path.glob(".scaffold.*"))


def test_capability_scaffold_durably_publishes_completed_directory_entry(
    tmp_path: Path, monkeypatch
) -> None:
    packet = scaffold_capability(
        CapabilityScaffoldRequest(
            capability_id="hello-world",
            action=OperationAction.STATUS,
            runner_kind=CapabilityRunnerKind.ENGINE,
            label="Hello world",
            description="Demonstrate the capability golden path.",
        )
    )
    from oh_goon.integrations import exact_file_write

    synced: list[Path] = []
    monkeypatch.setattr(exact_file_write, "_fsync_directory", synced.append)

    output = tmp_path / "scaffold"
    packet.write(output)

    assert synced[-1] == tmp_path
    assert all(path.is_file() for path in output.iterdir())
    assert not tuple(tmp_path.glob(".scaffold.*"))


def test_capability_scaffold_surfaces_final_directory_sync_failure(
    tmp_path: Path, monkeypatch
) -> None:
    packet = scaffold_capability(
        CapabilityScaffoldRequest(
            capability_id="hello-world",
            action=OperationAction.STATUS,
            runner_kind=CapabilityRunnerKind.ENGINE,
            label="Hello world",
            description="Demonstrate the capability golden path.",
        )
    )
    from oh_goon.integrations import exact_file_write

    def fail_final_parent_sync(directory: Path) -> None:
        if directory == tmp_path:
            raise OSError("scaffold directory sync failed")

    monkeypatch.setattr(exact_file_write, "_fsync_directory", fail_final_parent_sync)
    output = tmp_path / "scaffold"

    with pytest.raises(OSError, match="directory sync failed"):
        packet.write(output)

    assert output.is_dir()
    assert {path.name for path in output.iterdir()} == {
        "capability_definition.py",
        "test_capability.py",
        "IMPLEMENTATION.md",
    }
    assert not tuple(tmp_path.glob(".scaffold.*"))


def test_capability_scaffold_cli_reports_existing_output_without_traceback(
    tmp_path: Path, capsys
) -> None:
    from oh_goon.cli import main

    output = tmp_path / "scaffold"
    output.mkdir()
    preserved = output / "IMPLEMENTATION.md"
    preserved.write_text("keep me\n", encoding="utf-8")
    result = main(
        [
            "dev",
            "capability",
            "scaffold",
            "--id",
            "hello-world",
            "--action",
            "status",
            "--runner",
            "ENGINE",
            "--label",
            "Hello world",
            "--description",
            "Demonstrate the capability golden path.",
            "--safety",
            "READ_ONLY",
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert "already exists" in capsys.readouterr().out
    assert preserved.read_text(encoding="utf-8") == "keep me\n"
