from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from oh_goon.api import local_access
from oh_goon.core import local_access_protocol
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.command_runtime_authority import sanitized_child_environment
from oh_goon.integrations import fleet_plan_environment
from oh_goon.repository_tooling.release.accept_container import _acceptance_environment
from oh_goon.repository_tooling.release.accept_release import _installed_runtime_environment

ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = ROOT / "backend/src/oh_goon"
OWNER_PATH = SOURCE_ROOT / "core/local_access_protocol.py"


def test_owner_key_mutation_moves_api_and_stripping_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = local_access_protocol.LOCAL_ACCESS_ENV
    moved_key = "OH_GOON_LOCAL_ACCESS_TOKEN_V2"
    monkeypatch.setattr(local_access_protocol, "LOCAL_ACCESS_ENV", moved_key)
    monkeypatch.delenv(old_key, raising=False)
    monkeypatch.delenv(moved_key, raising=False)

    token = local_access.ensure_local_access_token()
    assert os.environ[moved_key] == token
    assert old_key not in os.environ

    child = sanitized_child_environment(
        ("tool",),
        {
            moved_key: "secret",
            old_key: "ordinary-after-owner-move",
            "PATH": os.environ.get("PATH", ""),
        },
    )
    assert moved_key not in child
    assert child[old_key] == "ordinary-after-owner-move"

    with pytest.raises(FleetPlanInvalid, match="process-local control-plane"):
        fleet_plan_environment._require_exportable_host_environment_name(moved_key)
    fleet_plan_environment._require_exportable_host_environment_name(old_key)


def test_acceptance_launchers_inject_through_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    moved_key = "OH_GOON_LOCAL_ACCESS_TOKEN_V2"
    monkeypatch.setattr(local_access_protocol, "LOCAL_ACCESS_ENV", moved_key)
    monkeypatch.delenv("OH_GOON_LOCAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv(moved_key, raising=False)

    container_env = _acceptance_environment(
        data=tmp_path / "data",
        repositories=tmp_path / "repositories",
        port="8787",
        dockerfile="Dockerfile",
        access_token="container-secret",
    )
    release_env = _installed_runtime_environment(
        tmp_path / "release-state", access_token="release-secret"
    )

    assert container_env[moved_key] == "container-secret"
    assert release_env[moved_key] == "release-secret"
    assert "OH_GOON_LOCAL_ACCESS_TOKEN" not in container_env
    assert "OH_GOON_LOCAL_ACCESS_TOKEN" not in release_env


def test_production_has_one_local_access_environment_identity_owner() -> None:
    identity = local_access_protocol.LOCAL_ACCESS_ENV
    literal_owners: list[Path] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == identity:
                literal_owners.append(path)

    assert literal_owners == [OWNER_PATH]
    assert not hasattr(local_access, "LOCAL_ACCESS_ENV")


def test_protocol_owner_carries_identity_only() -> None:
    names = {
        node.id
        for node in ast.walk(ast.parse(OWNER_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    }
    text = OWNER_PATH.read_text(encoding="utf-8")
    assert "secrets" not in names
    assert "token_urlsafe" not in text
    assert "token =" not in text
    assert "publish" not in text
    assert "authorize" not in text


def test_local_access_secret_stays_out_of_durable_launch_evidence(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from oh_goon.core.launch_manifest import (
        LaunchEndpointIntent,
        LaunchIdentity,
        LaunchIntent,
        LaunchServiceIntent,
    )
    from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
    from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

    database = tmp_path / "state" / "oh-goon.db"
    events = LocalLaunchEventStore.from_database(database)
    access = LocalLaunchAccessStore.from_database(database)
    secret = "m115-private-local-access-secret"
    launch_id = "launch-m115"

    manifest = events.create(
        identity=LaunchIdentity(
            launch_id=launch_id,
            workspace="/workspace/oh-goon",
            oh_goon_version="1267.0.1055",
            owner_generation=1,
        ),
        intent=LaunchIntent(
            profile="dev",
            services=(
                LaunchServiceIntent(
                    service="backend",
                    command=("uvicorn", "oh_goon.api.app:app"),
                    endpoint=LaunchEndpointIntent(service="backend", host="127.0.0.1", port=8787),
                ),
            ),
        ),
        asserted_by="m115-owner",
        timestamp=datetime(2026, 10, 6, tzinfo=UTC),
    )
    access.publish(launch_id=launch_id, owner_generation=1, token=secret)

    assert secret in access.path(launch_id).read_text(encoding="utf-8")
    assert secret not in events.path(launch_id).read_text(encoding="utf-8")
    assert secret not in manifest.to_json()
