from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.structural_launch_contract import (
    _check_launch_manifest_foundation,
)

ROOT = Path(__file__).resolve().parents[2]

_INPUTS = (
    "backend/src/oh_goon/api/app.py",
    "backend/src/oh_goon/cli_local_state.py",
    "backend/src/oh_goon/cli_logs.py",
    "backend/src/oh_goon/cli_open.py",
    "backend/src/oh_goon/cli_start.py",
    "backend/src/oh_goon/cli_status.py",
    "backend/src/oh_goon/cli_stop.py",
    "backend/src/oh_goon/core/game_tape_projection.py",
    "backend/src/oh_goon/core/launch_activity.py",
    "backend/src/oh_goon/core/launch_control.py",
    "backend/src/oh_goon/core/launch_events.py",
    "backend/src/oh_goon/core/launch_manifest.py",
    "backend/src/oh_goon/core/launch_navigation.py",
    "backend/src/oh_goon/core/launch_projection.py",
    "backend/src/oh_goon/core/launch_status.py",
    "backend/src/oh_goon/core/process_birth.py",
    "backend/src/oh_goon/core/run_projection.py",
    "backend/src/oh_goon/repository_tooling/local_dev_launch.py",
    "backend/src/oh_goon/repository_tooling/local_launch_exec.py",
    "backend/src/oh_goon/runtime/journal.py",
    "backend/src/oh_goon/runtime/local_launch_access.py",
    "backend/src/oh_goon/runtime/local_launch_binding.py",
    "backend/src/oh_goon/runtime/local_launch_context.py",
    "backend/src/oh_goon/runtime/local_launch_owner.py",
    "backend/src/oh_goon/runtime/local_launch_plan.py",
    "backend/src/oh_goon/runtime/local_launch_reuse.py",
    "backend/src/oh_goon/runtime/local_launch_supervisor.py",
    "backend/src/oh_goon/runtime/run_launch_receipt.py",
    "backend/src/oh_goon/services/fleet_run_lifecycle.py",
    "docs/development/LAUNCH_OWNERSHIP.md",
    "scripts/dev.sh",
)


@pytest.fixture
def launch_root(tmp_path: Path) -> Path:
    for relative in _INPUTS:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    assert _check_launch_manifest_foundation(tmp_path) == []
    return tmp_path


def _replace(root: Path, relative: str, old: str, new: str = "REMOVED") -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture token not found: {relative}: {old}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _messages(root: Path) -> list[str]:
    return [item.message for item in _check_launch_manifest_foundation(root)]


def test_launch_schema_owner_value_changes_need_no_architecture_literal_edit(
    launch_root: Path,
) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_manifest.py",
        'LAUNCH_MANIFEST_SCHEMA = "oh-goon.launch/v1"',
        'LAUNCH_MANIFEST_SCHEMA = "oh-goon.launch/v99"',
    )
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_events.py",
        'LAUNCH_EVENT_KIND = "launch_event"',
        'LAUNCH_EVENT_KIND = "future_launch_event"',
    )
    assert _messages(launch_root) == []


def test_launch_schema_owner_representation_is_refactor_tolerant(
    launch_root: Path,
) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_manifest.py",
        'LAUNCH_MANIFEST_SCHEMA = "oh-goon.launch/v1"',
        "LAUNCH_MANIFEST_SCHEMA: str = 'oh-goon.launch/v1'",
    )
    assert _messages(launch_root) == []


def test_launch_schema_owner_must_be_one_string_assignment(launch_root: Path) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_manifest.py",
        'LAUNCH_MANIFEST_SCHEMA = "oh-goon.launch/v1"',
        "LAUNCH_MANIFEST_SCHEMA = 1\n# LAUNCH_MANIFEST_SCHEMA = 'oh-goon.launch/v1'",
    )
    assert any(
        "must be assigned exactly once as a string" in message
        for message in _messages(launch_root)
    )


def test_launch_schema_owner_rejects_duplicate_assignment_elsewhere(
    launch_root: Path,
) -> None:
    path = launch_root / "backend/src/oh_goon/core/launch_projection.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nLAUNCH_MANIFEST_SCHEMA = 'decoy-owner'\n",
        encoding="utf-8",
    )
    assert any(
        "must have one natural owner" in message for message in _messages(launch_root)
    )


def test_launch_schema_owner_rejects_wrong_location_even_with_comment_decoy(
    launch_root: Path,
) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_manifest.py",
        'LAUNCH_MANIFEST_SCHEMA = "oh-goon.launch/v1"',
        "# LAUNCH_MANIFEST_SCHEMA = 'oh-goon.launch/v1'",
    )
    path = launch_root / "backend/src/oh_goon/core/launch_projection.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\nLAUNCH_MANIFEST_SCHEMA = 'oh-goon.launch/v1'\n",
        encoding="utf-8",
    )
    messages = _messages(launch_root)
    assert any("owner is missing or invalid" in message for message in messages)
    assert any("must have one natural owner" in message for message in messages)


def test_launch_foundation_rejects_process_authority_in_projection(
    launch_root: Path,
) -> None:
    path = launch_root / "backend/src/oh_goon/core/launch_projection.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nimport subprocess\n", encoding="utf-8"
    )
    assert any("must remain read-only" in message for message in _messages(launch_root))


def test_launch_foundation_rejects_game_tape_live_runtime_imports(
    launch_root: Path,
) -> None:
    path = launch_root / "backend/src/oh_goon/core/game_tape_projection.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\nfrom oh_goon.runtime.local_launch_state import LocalLaunchEventStore\n",
        encoding="utf-8",
    )
    assert any(
        "never read live launch runtime state" in message
        for message in _messages(launch_root)
    )


def test_launch_foundation_requires_durable_binding_schema(launch_root: Path) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/runtime/local_launch_binding.py",
        'LOCAL_LAUNCH_BINDING_SCHEMA = "oh-goon.local-launch-binding/v1"',
    )
    assert any(
        "status identity owner is missing or invalid" in message
        for message in _messages(launch_root)
    )


def test_launch_foundation_requires_durable_status_schema(launch_root: Path) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_status.py",
        'LAUNCH_STATUS_SCHEMA = "oh-goon.launch-status/v1"',
    )
    assert any(
        "status identity owner is missing or invalid" in message
        for message in _messages(launch_root)
    )


def test_launch_foundation_requires_durable_access_schema(launch_root: Path) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/runtime/local_launch_access.py",
        'LOCAL_LAUNCH_ACCESS_SCHEMA = "oh-goon.local-launch-access/v1"',
    )
    assert any(
        "access schema owner is missing or invalid" in message
        for message in _messages(launch_root)
    )


def test_launch_foundation_requires_durable_log_schema(launch_root: Path) -> None:
    _replace(
        launch_root,
        "backend/src/oh_goon/core/launch_activity.py",
        'LAUNCH_LOG_SCHEMA = "oh-goon.launch-log/v1"',
    )
    assert any(
        "Launch log schema owner is missing or invalid" in message
        for message in _messages(launch_root)
    )


def test_launch_architecture_is_invariant_to_navigation_private_local_rename(
    launch_root: Path,
) -> None:
    path = launch_root / "backend/src/oh_goon/core/launch_navigation.py"
    before = _messages(launch_root)
    text = path.read_text(encoding="utf-8")
    assert "requested" in text
    path.write_text(text.replace("requested", "intent_service"), encoding="utf-8")
    assert _messages(launch_root) == before


def test_launch_foundation_rejects_generic_stop_process_authority(
    launch_root: Path,
) -> None:
    path = launch_root / "backend/src/oh_goon/cli_stop.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nimport subprocess\n", encoding="utf-8"
    )
    assert any(
        "generic process authority" in message for message in _messages(launch_root)
    )


def test_launch_foundation_rejects_pid_signal_probe_as_birth_identity(
    launch_root: Path,
) -> None:
    path = launch_root / "backend/src/oh_goon/core/process_birth.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\ndef _bad_probe():\n    return os.kill(1, 0)\n",
        encoding="utf-8",
    )
    assert any(
        "PID existence/signal checks are not process identity" in message
        for message in _messages(launch_root)
    )


def test_launch_architecture_does_not_certify_private_behavior_source_spelling() -> (
    None
):
    architecture_root = ROOT / "backend/src/oh_goon/repository_tooling/architecture"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            architecture_root / "_launch_contract_navigation.py",
            architecture_root / "_launch_contract_control.py",
            architecture_root / "_launch_contract_foundation.py",
        )
    )
    for spelling in (
        "requested.endpoint.host",
        "requested.endpoint.port",
        "ensure_local_access_token",
        "new_local_access_token",
        "manifest.to_json(",
        ".launch_stopped(",
        ".launch_failed(",
        "st_mtime",
    ):
        assert spelling not in source
