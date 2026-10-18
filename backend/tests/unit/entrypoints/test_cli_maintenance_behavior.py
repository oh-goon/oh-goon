from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon import cli_runtime_recovery, cli_storage
from oh_goon.core.errors import OperationNotFound


def ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _storage_census(tmp_path: Path):
    inventory = [
        SimpleNamespace(
            storage_id="state",
            path=tmp_path / "state",
            owner="runtime",
            durability="durable",
            retention="forever",
            cleanup_authority="none",
            generic_prune=False,
        ),
        SimpleNamespace(
            storage_id="logs",
            path=None,
            owner="logs",
            durability="transient",
            retention="bounded",
            cleanup_authority="generic",
            generic_prune=True,
        ),
    ]
    unclassified = [SimpleNamespace(path=tmp_path / "unknown", root_kind="state", reason="unknown")]
    return SimpleNamespace(inventory=inventory, unclassified=unclassified)


def test_storage_status_text_json_and_measurement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    census = _storage_census(tmp_path)
    monkeypatch.setattr(cli_storage, "storage_census", lambda _environment=None: census)
    monkeypatch.setattr(
        cli_storage,
        "measure_storage",
        lambda path: None if path is None else SimpleNamespace(entries=2, bytes=17),
    )
    assert cli_storage._run_storage_status(ns(measure=True, as_json=False)) == 0
    text = capsys.readouterr().out
    assert (
        "OH-GOON LOCAL STORAGE" in text
        and "2 entries / 17 bytes" in text
        and "UNCLASSIFIED" in text
    )
    assert cli_storage._run_storage_status(ns(measure=True, as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["storage"][0]["measurement"]["bytes"] == 17
    assert payload["storage"][1]["measurement"] is None


def test_storage_measurement_disabled_and_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert cli_storage._measurement_payload(tmp_path, enabled=False) is None
    monkeypatch.setattr(cli_storage, "measure_storage", lambda _path: None)
    assert cli_storage._measurement_payload(tmp_path, enabled=True) is None


def test_storage_prune_authority_exception_json_and_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_storage.run_storage_command(
            ns(command="storage", storage_command="prune", yes=False, as_json=False)
        )
        == 2
    )
    assert "Nothing was deleted" in capsys.readouterr().out
    monkeypatch.setattr(
        cli_storage,
        "prune_generic_storage",
        lambda _env: (_ for _ in ()).throw(RuntimeError("unsafe")),
    )
    assert (
        cli_storage.run_storage_command(
            ns(command="storage", storage_command="prune", yes=True, as_json=False)
        )
        == 2
    )
    assert "unsafe" in capsys.readouterr().out
    report = SimpleNamespace(
        removed_run_logs=3,
        removed_run_workspaces=["a"],
        refused_run_workspaces=["b"],
        removed_content_staging=["c"],
        refused_content_staging=[],
        removed_content_objects=["d"],
        removed_launch_access=["e"],
        refused_launch_access=["f"],
        cleanup_incomplete_run_workspaces=[],
        to_payload=lambda: {"removed_run_logs": 3},
    )
    monkeypatch.setattr(cli_storage, "prune_generic_storage", lambda _env: report)
    monkeypatch.setattr(
        "oh_goon.services.certification_bootstrap.reconcile_certification_run_workspaces",
        lambda *, state_home: SimpleNamespace(
            removed=["repo/old"], refused=[], cleanup_incomplete=[]
        ),
    )
    assert (
        cli_storage.run_storage_command(
            ns(command="storage", storage_command="prune", yes=True, as_json=True)
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["removed_run_logs"] == 3
    assert (
        cli_storage.run_storage_command(
            ns(command="storage", storage_command="prune", yes=True, as_json=False)
        )
        == 0
    )
    assert "Removed run logs: 3" in capsys.readouterr().out


def test_storage_dispatch_other_and_unknown() -> None:
    assert cli_storage.run_storage_command(ns(command="other")) is None
    assert cli_storage.run_storage_command(ns(command="storage", storage_command="unknown")) is None


def _quarantine(operation_id="op", *, reconciliation=None):
    return SimpleNamespace(
        operation_id=operation_id,
        record_version=4,
        source=SimpleNamespace(value="git"),
        repository=SimpleNamespace(path="/repo"),
        action=SimpleNamespace(value="write"),
        safety=SimpleNamespace(value="unsafe"),
        status=SimpleNamespace(value="quarantined"),
        reconciliation=reconciliation,
    )


def _status(*, quarantines=(), findings=(), actions=(), healthy=False):
    return SimpleNamespace(
        effect_quarantines=list(quarantines),
        repair_plan=SimpleNamespace(findings=list(findings), actions=list(actions)),
        invariants=SimpleNamespace(findings=list(findings), healthy=healthy),
    )


def test_recovery_quarantine_listing_empty_and_detailed(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_runtime_recovery._list_effect_quarantines(_status()) == 0
    assert "No repository effects" in capsys.readouterr().out
    reconciliation = SimpleNamespace(outcome=SimpleNamespace(value="restored"), reason="verified")
    assert (
        cli_runtime_recovery._list_effect_quarantines(
            _status(quarantines=[_quarantine(reconciliation=reconciliation)])
        )
        == 2
    )
    text = capsys.readouterr().out
    assert "reconciliation=restored" in text and "verified" in text


def test_recovery_helpers_find_and_retain_quarantine() -> None:
    status = _status(quarantines=[_quarantine("op")])
    assert cli_runtime_recovery._effect_quarantine(status, "op").record_version == 4
    assert cli_runtime_recovery._still_quarantined(status, "op") is True
    assert cli_runtime_recovery._still_quarantined(status, "missing") is False
    with pytest.raises(OperationNotFound):
        cli_runtime_recovery._effect_quarantine(status, "missing")


def test_recovery_command_acknowledge_reconcile_and_domain_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    initial = _status(quarantines=[_quarantine("op")])
    recovery = SimpleNamespace(
        status=lambda: initial,
        acknowledge=lambda *_args, **_kwargs: _status(),
        reconcile=lambda *_args, **_kwargs: _status(),
    )
    runtime = SimpleNamespace(recovery=recovery)
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime,
            ns(command="dev", dev_command="acknowledge-effect", operation_id="op", note="ok"),
        )
        == 0
    )
    assert "Acknowledged" in capsys.readouterr().out
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime, ns(command="dev", dev_command="reconcile-effect", operation_id="op")
        )
        == 0
    )
    assert "quarantine cleared" in capsys.readouterr().out
    recovery.status = lambda: _status()
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime,
            ns(command="dev", dev_command="acknowledge-effect", operation_id="missing", note=""),
        )
        == 2
    )
    assert capsys.readouterr().out.strip() == "missing"


def test_recovery_reconcile_remaining_and_invariant_modes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    remaining = _quarantine(
        "op",
        reconciliation=SimpleNamespace(
            outcome=SimpleNamespace(value="mismatch"), reason="still dirty"
        ),
    )
    runtime = SimpleNamespace(
        recovery=SimpleNamespace(
            status=lambda: _status(quarantines=[_quarantine("op")]),
            reconcile=lambda *_a, **_k: _status(quarantines=[remaining]),
        )
    )
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime, ns(command="dev", dev_command="reconcile-effect", operation_id="op")
        )
        == 2
    )
    assert "Outcome: mismatch" in capsys.readouterr().out
    finding = SimpleNamespace(severity=SimpleNamespace(value="ERROR"), code="X", message="broken")
    action = SimpleNamespace(
        action="rebuild", target="db", requires_confirmation=True, explanation="safe"
    )
    runtime.recovery.status = lambda: _status(findings=[finding], actions=[action], healthy=False)
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime, ns(command="dev", dev_command="invariants", invariant_action="repair-plan")
        )
        == 2
    )
    assert "confirmation=true" in capsys.readouterr().out
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime, ns(command="dev", dev_command="invariants", invariant_action="check")
        )
        == 2
    )
    assert "ERROR X" in capsys.readouterr().out


def test_recovery_invariants_clean_and_unknown_dispatch(capsys: pytest.CaptureFixture[str]) -> None:
    runtime = SimpleNamespace(recovery=SimpleNamespace(status=lambda: _status(healthy=True)))
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime, ns(command="dev", dev_command="invariants", invariant_action="repair-plan")
        )
        == 0
    )
    assert "no repair plan required" in capsys.readouterr().out.lower()
    assert (
        cli_runtime_recovery.run_recovery_command(
            runtime, ns(command="dev", dev_command="invariants", invariant_action="check")
        )
        == 0
    )
    assert "invariants hold" in capsys.readouterr().out
    assert cli_runtime_recovery.run_recovery_command(runtime, ns(command="other")) is None
    assert (
        cli_runtime_recovery.run_recovery_command(runtime, ns(command="dev", dev_command="mystery"))
        is None
    )
