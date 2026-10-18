from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oh_goon.core.launch_events import LaunchEventKind
from oh_goon.core.launch_manifest import LaunchIdentity, LaunchIntent, LaunchServiceIntent
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore, workspace_identity
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

T0 = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)


def _stores(tmp_path: Path) -> tuple[LocalLaunchEventStore, LocalLaunchBindingStore]:
    database = tmp_path / "state" / "oh-goon.db"
    return LocalLaunchEventStore.from_database(database), LocalLaunchBindingStore.from_database(database)


def _intent(profile: str = "dev") -> LaunchIntent:
    return LaunchIntent(
        profile=profile,
        services=(LaunchServiceIntent(service="backend", command=("uvicorn", "app:app")),),
    )


def _identity(workspace: Path, launch_id: str) -> LaunchIdentity:
    return LaunchIdentity(
        launch_id=launch_id,
        workspace=str(workspace.resolve()),
        oh_goon_version="1267.0.38",
        owner_generation=1,
    )


def _create_record(
    events: LocalLaunchEventStore,
    *,
    workspace: Path,
    launch_id: str,
    intent: LaunchIntent | None = None,
) -> tuple[LaunchIdentity, LaunchIntent]:
    resolved = _intent() if intent is None else intent
    identity = _identity(workspace, launch_id)
    events.create(identity=identity, intent=resolved, asserted_by="owner", timestamp=T0)
    return identity, resolved


def test_binding_path_is_exact_full_workspace_hash(tmp_path: Path) -> None:
    _events, bindings = _stores(tmp_path)
    workspace = (tmp_path / "repo").resolve()
    expected = workspace_identity(workspace).removeprefix("sha256:")
    assert bindings.path(workspace) == bindings.root / f"{expected}.json"
    assert len(expected) == 64


def test_bind_and_resolve_preserve_exact_launch_generation_and_intent(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    workspace = tmp_path / "repo"
    identity, intent = _create_record(events, workspace=workspace, launch_id="launch-a")
    bound = bindings.bind(identity=identity, intent=intent, event_store=events, timestamp=T0)
    assert bindings.resolve(workspace) == bound
    assert bound.intent_identity == intent.identity()
    assert bound.workspace == str(workspace.resolve())


def test_same_binding_is_idempotent(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    workspace = tmp_path / "repo"
    identity, intent = _create_record(events, workspace=workspace, launch_id="launch-a")
    first = bindings.bind(identity=identity, intent=intent, event_store=events, timestamp=T0)
    second = bindings.bind(
        identity=identity,
        intent=intent,
        event_store=events,
        timestamp=T0 + timedelta(seconds=1),
    )
    assert second == first


def test_nonterminal_binding_blocks_replacement(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    workspace = tmp_path / "repo"
    first_identity, first_intent = _create_record(events, workspace=workspace, launch_id="launch-a")
    bindings.bind(identity=first_identity, intent=first_intent, event_store=events, timestamp=T0)
    second_identity, second_intent = _create_record(events, workspace=workspace, launch_id="launch-b")
    with pytest.raises(ValueError, match="nonterminal"):
        bindings.bind(
            identity=second_identity,
            intent=second_intent,
            event_store=events,
            timestamp=T0 + timedelta(seconds=2),
        )
    assert bindings.resolve(workspace).launch_id == "launch-a"


def test_terminal_binding_can_be_replaced(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    workspace = tmp_path / "repo"
    first_identity, first_intent = _create_record(events, workspace=workspace, launch_id="launch-a")
    bindings.bind(identity=first_identity, intent=first_intent, event_store=events, timestamp=T0)
    events.append(
        "launch-a",
        event=LaunchEventKind.LAUNCH_ABORTED,
        asserted_by="owner",
        timestamp=T0 + timedelta(seconds=1),
    )
    second_identity, second_intent = _create_record(events, workspace=workspace, launch_id="launch-b")
    replacement = bindings.bind(
        identity=second_identity,
        intent=second_intent,
        event_store=events,
        timestamp=T0 + timedelta(seconds=2),
    )
    assert replacement.launch_id == "launch-b"


def test_missing_bound_launch_never_authorizes_automatic_replacement(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    workspace = tmp_path / "repo"
    first_identity, first_intent = _create_record(events, workspace=workspace, launch_id="launch-a")
    bindings.bind(identity=first_identity, intent=first_intent, event_store=events, timestamp=T0)
    events.path("launch-a").unlink()
    second_identity, second_intent = _create_record(events, workspace=workspace, launch_id="launch-b")
    with pytest.raises(ValueError, match="cannot be proven terminal"):
        bindings.bind(
            identity=second_identity,
            intent=second_intent,
            event_store=events,
            timestamp=T0 + timedelta(seconds=1),
        )


def test_malformed_binding_is_reported_not_replaced_by_launch_discovery(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    workspace = tmp_path / "repo"
    _create_record(events, workspace=workspace, launch_id="launch-newer-looking")
    path = bindings.path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported local launch binding schema"):
        bindings.resolve(workspace)
