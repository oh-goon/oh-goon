from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from oh_goon.repository_tooling import tool_session_batches as subject
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity


@pytest.mark.process
@pytest.mark.parametrize("mutation", ("authored", "symlink"))
def test_native_unit_cannot_publish_pass_for_changed_or_unreadable_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / "README.md"
    source.write_text("before\n", encoding="utf-8")
    counter = tmp_path / "executed"
    external = tmp_path / "external.md"
    external.write_text("unowned bytes\n", encoding="utf-8")
    command = (
        "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
        "p.write_text(p.read_text()+'.' if p.exists() else '.')"
    )
    stable = subject.QualificationUnit(
        "native", (sys.executable, "-c", command, str(counter), str(external)), 5
    )
    selected = [stable]
    state = tmp_path / "state"
    monkeypatch.setattr(subject, "ROOT", checkout)
    monkeypatch.setattr(subject, "_state_root", lambda: state)
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: tuple(selected))
    before = canonical_source_identity(checkout)
    assert subject.main([]) == 0
    assert counter.read_text() == "."
    receipts = list(state.rglob("*.json"))
    assert len(receipts) == 1
    original_receipt = receipts[0].read_bytes()

    change = (
        "; Path('README.md').write_text('after\\n')"
        if mutation == "authored"
        else "; Path('README.md').unlink(); Path('README.md').symlink_to(sys.argv[2])"
    )
    selected[:] = [
        subject.QualificationUnit(
            "native",
            (sys.executable, "-c", command + change, str(counter), str(external)),
            5,
        )
    ]
    assert subject.main([]) == 1
    assert counter.read_text() == ".."
    assert len(list(state.rglob("*.json"))) == 1
    assert receipts[0].read_bytes() == original_receipt

    if source.is_symlink():
        source.unlink()
    source.write_text("repaired\n", encoding="utf-8")
    selected[:] = [stable]
    assert subject.main([]) == 0
    assert counter.read_text() == "..."
    admitted_sources = {
        json.loads(path.read_text())["source_identity"]
        for path in state.rglob("*.json")
    }
    assert admitted_sources == {before, canonical_source_identity(checkout)}


def _complete(calls: list[str], unit: subject.QualificationUnit) -> int:
    calls.append(unit.name)
    return 0


def test_tool_session_batches_resume_exact_pass_units(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    units = (
        subject.QualificationUnit("first", ("first",), 10),
        subject.QualificationUnit("second", ("second",), 10),
    )
    calls: list[str] = []
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: units)
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(subject, "_run_unit", lambda unit: _complete(calls, unit))

    assert subject.main([]) == 0
    assert calls == ["first"]
    assert subject.main([]) == 0
    assert calls == ["first", "second"]
    assert subject.main([]) == 0
    assert calls == ["first", "second"]


def test_failed_unit_is_not_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = subject.QualificationUnit("only", ("only",), 10)
    outcomes = iter((7, 0))
    calls: list[str] = []
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: (unit,))
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)

    def run(current: subject.QualificationUnit) -> int:
        calls.append(current.name)
        return next(outcomes)

    monkeypatch.setattr(subject, "_run_unit", run)
    assert subject.main([]) == 7
    assert subject.main([]) == 0
    assert calls == ["only", "only"]


def test_plan_identity_changes_when_unit_contract_changes() -> None:
    first = (subject.QualificationUnit("unit", ("python", "-m", "x"), 10),)
    second = (subject.QualificationUnit("unit", ("python", "-m", "x"), 11),)
    assert subject._plan_identity(first) != subject._plan_identity(second)


def test_session_budget_soft_stops_before_unfittable_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unit = subject.QualificationUnit(
        "long", ("long",), 120, expected_runtime_seconds=40.0
    )
    calls: list[str] = []
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: (unit,))
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(subject, "_run_unit", lambda current: _complete(calls, current))

    assert (
        subject.main(
            ["--session-budget-seconds", "50", "--checkpoint-reserve-seconds", "20"]
        )
        == 0
    )
    assert calls == []
    assert "SOFT STOP" in capsys.readouterr().out
    assert not list(tmp_path.rglob("*.json"))


def test_unit_receipt_persists_runtime_separately_from_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = subject.QualificationUnit(
        "fast", ("fast",), 120, expected_runtime_seconds=10.0
    )
    ticks = iter((0.0, 0.0, 2.5))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: (unit,))
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(subject, "_run_unit", lambda _unit: 0)

    assert subject.main([]) == 0
    payload = __import__("json").loads(
        next(tmp_path.rglob("fast.json")).read_text(encoding="utf-8")
    )
    assert payload["timeout_seconds"] == 120
    assert payload["expected_runtime_seconds"] == 10.0
    assert payload["observed_runtime_seconds"] == 2.5


def test_repository_understanding_safety_timeout_is_separate_from_runtime_estimate() -> (
    None
):
    unit = next(
        item
        for item in subject.maintenance_release_units()
        if item.name == "repository-understanding"
    )
    assert unit.expected_runtime_seconds == 90.0
    assert unit.timeout_seconds == 1200
    assert unit.timeout_seconds > unit.expected_runtime_seconds


def test_status_json_reports_identity_receipts_and_first_unproven_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    units = (
        subject.QualificationUnit("first", ("first",), 10),
        subject.QualificationUnit("second", ("second",), 10),
    )
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: units)
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(subject, "_run_unit", lambda _unit: 0)

    assert subject.main([]) == 0
    capsys.readouterr()
    assert subject.main(["--status-json"]) == 0
    output = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(output)
    assert payload["schema"] == subject.STATUS_SCHEMA
    assert payload["source_identity"] == "sha256:source"
    assert payload["plan_identity"].startswith("sha256:")
    assert payload["toolchain_identity"] == "sha256:toolchain"
    assert payload["first_unproven_owner"] == "second"
    assert payload["units"][0]["state"] == "PASS"
    assert payload["units"][0]["receipt_hash"].startswith("sha256:")
    assert payload["units"][1] == {
        "owner": "second",
        "state": "PENDING",
        "receipt_hash": None,
    }


def test_receipt_crash_before_atomic_replace_is_not_reusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = subject.QualificationUnit("only", ("only",), 10)
    receipt = tmp_path / "only.json"
    real_replace = os.replace

    def crash_before_replace(_source: Path, _destination: Path) -> None:
        raise RuntimeError("simulated host loss before commit")

    monkeypatch.setattr(os, "replace", crash_before_replace)
    try:
        subject._write_receipt(
            receipt,
            source_identity="sha256:source",
            plan_identity="sha256:plan",
            toolchain_identity="sha256:toolchain",
            unit=unit,
            observed_runtime_seconds=1.0,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("simulated crash did not interrupt receipt commit")

    assert not receipt.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    assert not subject._receipt_valid(
        receipt,
        source_identity="sha256:source",
        plan_identity="sha256:plan",
        toolchain_identity="sha256:toolchain",
        unit=unit,
    )
    monkeypatch.setattr(os, "replace", real_replace)


def test_receipt_after_atomic_replace_is_exactly_reusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = subject.QualificationUnit("only", ("only",), 10)
    receipt = tmp_path / "only.json"
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def observed_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", observed_fsync)
    subject._write_receipt(
        receipt,
        source_identity="sha256:source",
        plan_identity="sha256:plan",
        toolchain_identity="sha256:toolchain",
        unit=unit,
        observed_runtime_seconds=1.0,
    )

    assert subject._receipt_valid(
        receipt,
        source_identity="sha256:source",
        plan_identity="sha256:plan",
        toolchain_identity="sha256:toolchain",
        unit=unit,
    )
    assert fsync_calls
    if os.name == "posix":
        assert len(fsync_calls) >= 2


def test_interrupted_unit_without_receipt_resumes_same_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    units = (
        subject.QualificationUnit("first", ("first",), 10),
        subject.QualificationUnit("second", ("second",), 10),
    )
    calls: list[str] = []
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: units)
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)

    def interrupted(unit: subject.QualificationUnit) -> int:
        calls.append(unit.name)
        return 124

    monkeypatch.setattr(subject, "_run_unit", interrupted)
    assert subject.main([]) == 124
    assert calls == ["first"]
    cap: list[str] = []
    monkeypatch.setattr(subject, "_run_unit", lambda unit: _complete(cap, unit))
    assert subject.main([]) == 0
    assert cap == ["first"]


def test_resume_credits_only_contiguous_owner_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    units = (
        subject.QualificationUnit(
            "first", ("first",), 10, expected_runtime_seconds=1.0
        ),
        subject.QualificationUnit(
            "second", ("second",), 10, expected_runtime_seconds=1.0
        ),
    )
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: units)
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)
    directory = subject._receipt_dir(
        source_identity="sha256:source",
        plan_identity=subject._plan_identity(units),
        toolchain_identity="sha256:toolchain",
    )
    subject._write_receipt(
        subject._receipt_path(directory, units[1]),
        source_identity="sha256:source",
        plan_identity=subject._plan_identity(units),
        toolchain_identity="sha256:toolchain",
        unit=units[1],
        observed_runtime_seconds=0.1,
    )
    calls: list[str] = []
    monkeypatch.setattr(subject, "_run_unit", lambda unit: _complete(calls, unit))

    assert subject.main(["--max-units", "2"]) == 0
    assert calls == ["first", "second"]


def test_budget_boundary_commits_completed_owner_then_resumes_exact_next_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    units = (
        subject.QualificationUnit(
            "first", ("first",), 10, expected_runtime_seconds=2.0
        ),
        subject.QualificationUnit(
            "second", ("second",), 10, expected_runtime_seconds=8.0
        ),
    )
    ticks = iter((0.0, 0.0, 0.0, 1.0, 1.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(subject, "maintenance_release_units", lambda: units)
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:source"
    )
    monkeypatch.setattr(
        subject, "python_toolchain_identity", lambda: "sha256:toolchain"
    )
    monkeypatch.setattr(subject, "_state_root", lambda: tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(subject, "_run_unit", lambda unit: _complete(calls, unit))

    assert (
        subject.main(
            [
                "--max-units",
                "2",
                "--session-budget-seconds",
                "10",
                "--checkpoint-reserve-seconds",
                "2",
            ]
        )
        == 0
    )
    assert calls == ["first"]

    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    assert (
        subject.main(
            [
                "--max-units",
                "2",
                "--session-budget-seconds",
                "20",
                "--checkpoint-reserve-seconds",
                "2",
            ]
        )
        == 0
    )
    assert calls == ["first", "second"]


def test_qualification_unit_runtime_environment_is_identity_bound() -> None:
    default = (subject.QualificationUnit("unit", ("tool",), 10),)
    custom = (
        subject.QualificationUnit(
            "unit", ("tool",), 10, environment=(("TERM", "xterm-test"),)
        ),
    )
    assert dict(default[0].environment) == {"TERM": "dumb"}
    assert subject._plan_identity(default) != subject._plan_identity(custom)


def test_run_unit_supplies_noninteractive_term(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Result:
        stdout = ""
        returncode = 0

    def run_command(*argv: str, **kwargs: object) -> Result:
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return Result()

    monkeypatch.setattr(subject, "run_command", run_command)
    unit = subject.QualificationUnit("architecture", ("tool", "architecture"), 30)
    assert subject._run_unit(unit) == 0
    assert captured["env"] == {"TERM": "dumb"}


@pytest.mark.process
def test_pytest_units_use_project_configuration_for_imports_and_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = tmp_path / "backend"
    support = backend / "tests"
    support.mkdir(parents=True)
    (support / "__init__.py").write_text("", encoding="utf-8")
    (support / "fakes.py").write_text(
        "VALUE = 'project-test-support'\n", encoding="utf-8"
    )
    (backend / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["."]\n'
        "addopts = \"--strict-markers -m 'not external_host'\"\n"
        'markers = ["external_host: separately selected host qualification"]\n',
        encoding="utf-8",
    )
    tooling = tmp_path / "scripts/tests"
    tooling.mkdir(parents=True)
    (tooling / "conftest.py").write_text(
        "import pytest\n"
        "from tests.fakes import VALUE\n"
        "@pytest.fixture\n"
        "def configured_support(): return VALUE\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    units = tuple(
        unit
        for unit in subject.maintenance_release_units()
        if unit.command[1:3] == ("-m", "pytest")
    )
    assert units
    for unit in units:
        selected_test = tmp_path / unit.command[-1]
        selected_test.write_text(
            "import pytest\n"
            "def test_configured_support(configured_support):\n"
            "    assert configured_support == 'project-test-support'\n"
            "@pytest.mark.external_host\n"
            "def test_separate_host_qualification():\n"
            "    raise AssertionError('project marker selection was not applied')\n",
            encoding="utf-8",
        )
        assert subject._run_unit(unit) == 0
        assert "1 passed, 1 deselected" in capsys.readouterr().out
