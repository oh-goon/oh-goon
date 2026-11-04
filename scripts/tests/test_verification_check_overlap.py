from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oh_goon.engine.command import CommandCancelledError, run_command
from oh_goon.repository_tooling import development_verification as owner
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, owner.ChangeVerificationPlan]:
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("original\n")
    owner._git(root, "init", "-q")
    owner._git(root, "add", "README.md")
    owner._git(
        root,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    head = owner._git(root, "rev-parse", "HEAD").strip()
    return root, owner.ChangeVerificationPlan(
        base=head,
        candidate=head,
        source_identity=canonical_source_identity(root),
        changed_paths=("README.md",),
        static=(),
        pytest=(),
        frontend=(),
        unverified=(),
        evidence={},
        behavioral_status="NOT_APPLICABLE",
        policy_status="ORDINARY",
    )


def _command(
    tmp_path: Path, label: str, program: str
) -> tuple[str, tuple[str, ...], Path]:
    script = tmp_path / (label.replace(" ", "_") + ".py")
    script.write_text(program)
    return label, (sys.executable, "-I", str(script)), tmp_path


def _two_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    # The execution tests provide capacity at its existing owner seam. Native
    # quota interpretation remains covered by the resource-budget owner's tests.
    monkeypatch.setattr(
        owner,
        "resolve_pytest_resource_budget",
        lambda _workers: SimpleNamespace(worker_ceiling=2),
        raising=False,
    )


def test_independent_native_checks_overlap_and_mutating_owners_wait(
    source: tuple[Path, owner.ChangeVerificationPlan],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan = source
    _two_slots(monkeypatch)
    commands = []
    for label, peer in (("Types", "Architecture"), ("Architecture", "Types")):
        commands.append(
            _command(
                tmp_path,
                label,
                "from pathlib import Path\nfrom time import monotonic,sleep\n"
                f"Path({label!r}).write_text('started')\n"
                "deadline=monotonic()+10\n"
                f"while not Path({peer!r}).exists():\n"
                "    if monotonic()>deadline: raise SystemExit(17)\n"
                "    sleep(0.01)\n"
                f"Path({label + '.done'!r}).write_text('done')\n",
            )
        )
    commands.append(
        _command(
            tmp_path,
            "Native package",
            "from pathlib import Path\n"
            "assert all(Path(name+'.done').exists() for name in "
            "('Types','Architecture'))\n"
            "Path('package.done').write_text('done')\n",
        )
    )
    monkeypatch.setattr(owner, "_commands", lambda *_args: commands)

    result = owner.execute_change(root, plan, mode="focused", workers=2)

    assert result["status"] == "CHECKS_PASSED"
    assert result["outcomes"] == [
        {"owner": row[0], "returncode": 0} for row in commands
    ]
    assert (tmp_path / "package.done").read_text() == "done"


@pytest.mark.parametrize("source_drift", [False, True])
def test_failure_or_source_drift_stops_later_checks_and_settles_native_sibling(
    source: tuple[Path, owner.ChangeVerificationPlan],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_drift: bool,
) -> None:
    root, plan = source
    _two_slots(monkeypatch)
    sleeping = _command(
        tmp_path,
        "Types",
        "from pathlib import Path\nimport subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,'-I','-c',"
        '"from pathlib import Path; import signal,sys,time; '
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
        "Path('descendant.ready').write_text('ready'); time.sleep(30)\"])\n"
        "while not Path('descendant.ready').exists(): time.sleep(0.01)\n"
        "Path('leader.ready').write_text('ready')\ntime.sleep(30)\n",
    )
    finishing = _command(
        tmp_path,
        "Architecture",
        "from pathlib import Path\nfrom time import sleep,monotonic\n"
        "deadline=monotonic()+10\n"
        "while not Path('leader.ready').exists():\n"
        "    if monotonic()>deadline: raise SystemExit(29)\n"
        "    sleep(0.01)\n"
        + (
            f"Path({str(root / 'README.md')!r}).write_text('drifted\\n')\n"
            if source_drift
            else "raise SystemExit(23)\n"
        ),
    )
    later = _command(
        tmp_path, "Native package", "raise AssertionError('later owner ran')\n"
    )
    monkeypatch.setattr(owner, "_commands", lambda *_args: [sleeping, finishing, later])
    cancellations = []

    def observed_command(*argv: str, **options: Any) -> Any:
        try:
            return run_command(*argv, **options)
        except CommandCancelledError as exc:
            cancellations.append(exc.supervisor)
            raise

    monkeypatch.setattr(owner, "run_command", observed_command)

    result = owner.execute_change(root, plan, mode="focused", workers=2)

    assert result["status"] == ("INCOMPLETE" if source_drift else "FAIL")
    assert [row["owner"] for row in result["outcomes"]] == ["Types", "Architecture"]
    assert result["outcomes"][0]["status"] == "CANCELLED"
    assert result["outcomes"][0]["returncode"] is None
    assert result["outcomes"][1]["returncode"] == (0 if source_drift else 23)
    assert len(cancellations) == 1 and cancellations[0].final_process_group_quiescent
    if source_drift:
        assert "source changed" in result["reason"]
        assert (root / "README.md").read_text() == "drifted\n"


def test_single_native_cpu_budget_keeps_static_checks_serial(
    source: tuple[Path, owner.ChangeVerificationPlan],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan = source
    monkeypatch.setenv("OH_GOON_TEST_CPUS", "1")
    commands = [
        _command(
            tmp_path,
            label,
            "from pathlib import Path\nfrom time import sleep\n"
            f"with Path('trace').open('a') as log: log.write({label!r}+':start'+chr(10))\n"
            "sleep(0.03)\n"
            f"with Path('trace').open('a') as log: log.write({label!r}+':end'+chr(10))\n",
        )
        for label in ("Ruff", "Types", "Architecture")
    ]
    monkeypatch.setattr(owner, "_commands", lambda *_args: commands)

    result = owner.execute_change(root, plan, mode="focused", workers=4)

    assert result["status"] == "CHECKS_PASSED"
    assert (tmp_path / "trace").read_text().splitlines() == [
        f"{label}:{state}"
        for label in ("Ruff", "Types", "Architecture")
        for state in ("start", "end")
    ]


def test_native_deadline_failure_cancels_sibling_without_claiming_an_exit_code(
    source: tuple[Path, owner.ChangeVerificationPlan],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan = source
    _two_slots(monkeypatch)
    commands = [
        _command(tmp_path, label, "import time\ntime.sleep(30)\n")
        for label in ("Types", "Architecture")
    ]
    commands.append(
        _command(tmp_path, "Native package", "raise AssertionError('later owner ran')")
    )
    monkeypatch.setattr(owner, "_commands", lambda *_args: commands)
    requested_deadlines = []

    def bounded_native_command(*argv: str, **options: Any) -> Any:
        if options.get("timeout") == 300:
            requested_deadlines.append(options["timeout"])
            if argv[-1] == commands[0][1][-1]:
                options["timeout"] = 0.3
        return run_command(*argv, **options)

    monkeypatch.setattr(owner, "run_command", bounded_native_command)

    result = owner.execute_change(root, plan, mode="focused", workers=2)

    assert result["status"] == "FAIL"
    assert requested_deadlines == [300, 300]
    first, second = result["outcomes"]
    assert first["owner"] == "Types" and first["status"] == "TIMEOUT"
    assert first["returncode"] is None
    assert first["native_cleanup"] == {
        "reason": "timeout",
        "process_group_quiescent": True,
    }
    assert second["owner"] == "Architecture" and second["status"] == "CANCELLED"
    assert second["returncode"] is None
    assert second["native_cleanup"]["process_group_quiescent"]


def test_main_thread_interruption_joins_native_cleanup_before_propagating(
    source: tuple[Path, owner.ChangeVerificationPlan],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from time import monotonic, sleep

    root, plan = source
    _two_slots(monkeypatch)
    commands = [
        _command(
            tmp_path,
            label,
            "from pathlib import Path\nimport time\n"
            f"Path({label + '.ready'!r}).write_text('ready')\ntime.sleep(30)\n",
        )
        for label in ("Types", "Architecture")
    ]
    monkeypatch.setattr(owner, "_commands", lambda *_args: commands)
    cancellations = []

    def observed_command(*argv: str, **options: Any) -> Any:
        try:
            return run_command(*argv, **options)
        except CommandCancelledError as exc:
            cancellations.append(exc.supervisor)
            raise

    def interrupt_wait(*_args: Any, **_options: Any) -> Any:
        deadline = monotonic() + 10
        while not all(
            (tmp_path / (label + ".ready")).exists()
            for label in ("Types", "Architecture")
        ):
            assert monotonic() < deadline, (
                "native checks did not reach their wait boundary"
            )
            sleep(0.01)
        raise KeyboardInterrupt

    monkeypatch.setattr(owner, "run_command", observed_command)
    monkeypatch.setattr(owner.concurrent.futures, "wait", interrupt_wait)

    with pytest.raises(KeyboardInterrupt):
        owner.execute_change(root, plan, mode="focused", workers=2)

    assert len(cancellations) == 2
    assert all(receipt.final_process_group_quiescent for receipt in cancellations)
