"""CO-AG4: original goon.lock writer is usable without application runtime."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.execution_lock_persistence import load_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService


def _source() -> Path:
    return PlanTemplateStore().source("python/dependency-inventory").path


def _no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **_kwargs: pytest.fail("lock creation started application/Fleet runtime"),
    )


def test_explicit_output_matches_original_service_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    source = _source()
    destination = tmp_path / "locks" / "candidate.goon.lock"
    expected_path = tmp_path / "reference" / "goon.lock"
    expected = FleetPlanService(
        FleetPlanResolver(goon_templates=GoonTemplateStore()),
        None, None, None, None,
    ).lock(source, output=expected_path)
    assert main(["lock", str(source), "--output", str(destination)]) == 0
    out = capsys.readouterr().out
    assert out == f"Generated: {destination}\nExecution digest: {expected.digest}\n"
    assert destination.read_bytes() == expected_path.read_bytes()
    assert load_execution_lock(destination) == expected
    assert not list(tmp_path.rglob("*.sqlite*"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_default_destination_is_source_adjacent_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    source = _source()
    root = tmp_path / "plan-source"
    root.mkdir()
    plan = root / "plan.yml"
    plan.write_bytes(source.read_bytes())
    # The canonical template imports built-ins, which remain resolvable from
    # the standalone GoonTemplateStore; input contracts are still validated.
    assert main(["lock", str(plan)]) == 0
    out = capsys.readouterr().out
    assert f"Generated: {root / 'goon.lock'}\n" in out
    assert load_execution_lock(root / "goon.lock").digest in out
    assert not list(root.rglob("*.sqlite*"))


def test_invalid_source_and_input_fail_before_writer_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _no_runtime(monkeypatch)
    bad = tmp_path / "plan.yml"
    bad.write_text("version: 1\nsteps: invalid\n", encoding="utf-8")
    destination = tmp_path / "proof" / "goon.lock"
    assert main(["lock", str(bad), "--output", str(destination)]) == 2
    assert capsys.readouterr().out
    assert not destination.parent.exists()
    assert main(["lock", str(_source()), "--input", "unknown=secret", "--output", str(destination)]) == 2
    assert "unknown" in capsys.readouterr().out.lower()
    assert not destination.parent.exists()


def test_original_lock_owner_is_called_once_and_no_parallel_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_runtime(monkeypatch)
    import oh_goon.cli_lock as cli_lock

    observed: list[tuple[Path, Path | None]] = []
    original = cli_lock._generate_execution_lock

    def exact_original(resolver, plan_path, *, extensions_path, inputs, output):
        observed.append((plan_path, output))
        return original(
            resolver, plan_path, extensions_path=extensions_path,
            inputs=inputs, output=output,
        )

    monkeypatch.setattr(cli_lock, "_generate_execution_lock", exact_original)
    target = tmp_path / "goon.lock"
    assert main(["lock", str(_source()), "--output", str(target)]) == 0
    assert observed == [(_source(), target)]


def test_original_service_and_stateful_adapter_remain_compatible(tmp_path: Path, capsys) -> None:
    from argparse import Namespace
    from types import SimpleNamespace

    from oh_goon.cli_runtime import run_stateful_command

    service = FleetPlanService(
        FleetPlanResolver(goon_templates=GoonTemplateStore()),
        None, None, None, None,
    )
    path = tmp_path / "goon.lock"
    args = Namespace(command="lock", plan=_source(), output=path, plan_inputs=None)
    assert run_stateful_command(SimpleNamespace(plans=service), args) == 0
    assert "Execution digest:" in capsys.readouterr().out
    assert load_execution_lock(path).digest


def test_atomic_writer_preserves_previous_lock_on_resolution_failure(tmp_path: Path, monkeypatch) -> None:
    _no_runtime(monkeypatch)
    path = tmp_path / "goon.lock"
    path.write_text("do-not-replace", encoding="utf-8")
    bad = tmp_path / "bad.yml"
    bad.write_text("version: 1\nsteps: invalid\n", encoding="utf-8")
    assert main(["lock", str(bad), "--output", str(path)]) == 2
    assert path.read_text() == "do-not-replace"
    assert not list(tmp_path.glob(".goon.lock.*.tmp"))
