from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from oh_goon.cli_parser import build_parser
from oh_goon.cli_storage import run_storage_command
from oh_goon.runtime.storage_lifecycle import StoragePruneReport


def test_storage_parser_exposes_status_and_prune() -> None:
    status = build_parser().parse_args(["storage", "status", "--measure", "--json"])
    assert status.command == "storage"
    assert status.storage_command == "status"
    assert status.measure is True
    assert status.as_json is True

    prune = build_parser().parse_args(["storage", "prune", "--yes", "--json"])
    assert prune.storage_command == "prune"
    assert prune.yes is True
    assert prune.as_json is True


def test_storage_prune_requires_explicit_confirmation(capsys) -> None:
    args = build_parser().parse_args(["storage", "prune"])

    assert run_storage_command(args) == 2
    output = capsys.readouterr().out
    assert "Nothing was deleted" in output
    assert "Durable state" in output
    assert "make storage-prune" in output
    assert "oh-goon storage prune --yes" not in output


def test_storage_prune_json_reports_owner_results(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "oh_goon.cli_storage.prune_generic_storage",
        lambda _environment: StoragePruneReport(
            removed_run_logs=2,
            removed_run_workspaces=("oh-goon-plan-a",),
            refused_run_workspaces=("foreign",),
            removed_content_staging=("tree-a",),
            refused_content_staging=("legacy",),
            removed_content_objects=("tree:abc",),
            removed_launch_access=("launch-a.json",),
            refused_launch_access=("launch-b.json",),
        ),
    )
    monkeypatch.setattr(
        "oh_goon.services.certification_bootstrap.reconcile_certification_run_workspaces",
        lambda *, state_home: SimpleNamespace(
            removed=("repo-a/oh-goon-plan-old",), refused=(), cleanup_incomplete=()
        ),
    )
    args = build_parser().parse_args(["storage", "prune", "--yes", "--json"])

    assert run_storage_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed_run_logs"] == 2
    assert payload["refused_content_staging"] == ["legacy"]
    assert payload["removed_certification_run_workspaces"] == ["repo-a/oh-goon-plan-old"]


def test_storage_prune_cli_reclaims_only_current_certification_scratch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from oh_goon.core.product_identity import repository_id
    from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase
    from oh_goon.services.certification_bootstrap import certification_state_root

    state_home = tmp_path / "state"
    data_home = tmp_path / "data"
    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    source = tmp_path / "repository"
    source.mkdir()
    repo_id = repository_id(source)
    current = certification_state_root(source_root=source, state_home=state_home)
    LocalDatabase(current / "oh-goon.db")
    current_workspace = current / "run-workspaces" / "oh-goon-plan-current-stale"
    current_workspace.mkdir(parents=True)

    old_schema = max(1, DATABASE_SCHEMA_VERSION - 1)
    historical = (
        state_home / "oh-goon" / "certification" / repo_id / f"schema-{old_schema}" / "state"
    )
    historical_workspace = historical / "run-workspaces" / "oh-goon-plan-old-stale"
    historical_workspace.mkdir(parents=True)

    args = build_parser().parse_args(["storage", "prune", "--yes", "--json"])
    assert run_storage_command(args) == 0

    payload = json.loads(capsys.readouterr().out)
    removed = set(payload["removed_certification_run_workspaces"])
    assert f"{repo_id}/oh-goon-plan-current-stale" in removed
    assert all(f"schema-{old_schema}" not in item for item in removed)
    assert not current_workspace.exists()
    assert historical_workspace.exists()


def test_storage_prune_text_names_each_refused_certification_workspace(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "oh_goon.cli_storage.prune_generic_storage",
        lambda _environment: StoragePruneReport(
            removed_run_logs=0,
            removed_run_workspaces=(),
            refused_run_workspaces=(),
            removed_content_staging=(),
            refused_content_staging=(),
            removed_content_objects=(),
            removed_launch_access=(),
            refused_launch_access=(),
        ),
    )
    monkeypatch.setattr(
        "oh_goon.services.certification_bootstrap.reconcile_certification_run_workspaces",
        lambda *, state_home: SimpleNamespace(
            removed=(),
            refused=("repo-a/oh-goon-plan-live", "repo-b/oh-goon-plan-unproven"),
            cleanup_incomplete=(),
        ),
    )
    args = build_parser().parse_args(["storage", "prune", "--yes"])

    assert run_storage_command(args) == 0
    output = capsys.readouterr().out
    assert "Refused certification run workspaces: 2" in output
    assert "Refused certification workspace: repo-a/oh-goon-plan-live" in output
    assert "Refused certification workspace: repo-b/oh-goon-plan-unproven" in output


def test_storage_status_json_exposes_resolved_capacity_policy(
    monkeypatch, tmp_path, capsys
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("OH_GOON_STORAGE_SOFT_FREE_BYTES", "200")
    monkeypatch.setenv("OH_GOON_STORAGE_HARD_FREE_BYTES", "100")
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "oh_goon.runtime.storage_health.shutil.disk_usage",
        lambda _path: Usage(total=1000, used=850, free=150),
    )

    args = build_parser().parse_args(["storage", "status", "--json"])
    assert run_storage_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["capacity"] == {
        "pressure": "SOFT_PRESSURE",
        "free_bytes": 150,
        "soft_free_bytes": 200,
        "hard_free_bytes": 100,
    }
