from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore


def test_access_store_round_trips_only_exact_launch_generation(tmp_path: Path) -> None:
    store = LocalLaunchAccessStore(root=tmp_path / "access")
    store.publish(launch_id="launch-a", owner_generation=2, token="secret-a")

    assert store.token(launch_id="launch-a", owner_generation=2) == "secret-a"
    assert store.path("launch-a").stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o777 == 0o700

    with pytest.raises(ValueError, match="another owner generation"):
        store.token(launch_id="launch-a", owner_generation=1)
    with pytest.raises(ValueError, match="unavailable"):
        store.token(launch_id="launch-b", owner_generation=2)


def test_access_store_revokes_only_exact_launch_generation(tmp_path: Path) -> None:
    store = LocalLaunchAccessStore(root=tmp_path / "access")
    store.publish(launch_id="launch-a", owner_generation=2, token="secret-a")

    with pytest.raises(ValueError, match="another owner generation"):
        store.revoke(launch_id="launch-a", owner_generation=1)
    assert store.token(launch_id="launch-a", owner_generation=2) == "secret-a"

    assert store.revoke(launch_id="launch-a", owner_generation=2) is True
    assert store.revoke(launch_id="launch-a", owner_generation=2) is False


def test_access_store_prunes_only_durably_terminal_launch_generation(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from oh_goon.core.launch_events import LaunchEventKind
    from oh_goon.core.launch_manifest import LaunchIdentity, LaunchIntent
    from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

    database = tmp_path / "state" / "oh-goon.db"
    events = LocalLaunchEventStore.from_database(database)
    access = LocalLaunchAccessStore.from_database(database)
    identity = LaunchIdentity(
        launch_id="launch-terminal",
        workspace=str((tmp_path / "workspace").resolve()),
        oh_goon_version="1267.0.49",
        owner_generation=1,
    )
    events.create(
        identity=identity,
        intent=LaunchIntent(profile="test", services=()),
        asserted_by="test",
        timestamp=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    access.publish(launch_id=identity.launch_id, owner_generation=1, token="secret")

    capability_name = access.path(identity.launch_id).name
    before = access.prune_terminal(events)
    assert before.removed == ()
    assert before.refused == (capability_name,)

    events.append(
        identity.launch_id,
        event=LaunchEventKind.LAUNCH_ABORTED,
        asserted_by="test",
        timestamp=datetime(2026, 9, 4, 12, 1, tzinfo=UTC),
    )
    after = access.prune_terminal(events)
    assert after.removed == (capability_name,)
    assert after.refused == ()
    assert not access.path(identity.launch_id).exists()
