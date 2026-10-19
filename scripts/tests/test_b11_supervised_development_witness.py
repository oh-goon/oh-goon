"""B11 regression: no JUnit-only pass, zombie child, or unbounded ring may certify."""

from __future__ import annotations

from typing import Any, cast

from pathlib import Path
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from oh_goon.repository_tooling import b11_ring_supervisor as supervisor

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "backend/src/oh_goon/repository_tooling/b11_ring_supervisor.py"
WITNESS = ROOT / "scripts/qualify_b11_development_wsl.sh"


def _run(tmp_path: Path, code: str, *, timeout: int = 4) -> dict[str, Any]:
    result = supervisor.supervise(
        name="RING",
        command=[sys.executable, "-c", code],
        directory=tmp_path,
        timeout_seconds=timeout,
    )

    return cast(dict[str, Any], result)


def _xml(
    tmp_path: Path,
    *,
    tests: int = 1,
    errors: int = 0,
    failures: int = 0,
    skipped: int = 0,
) -> str:
    path = tmp_path / "RING.xml"
    return "from pathlib import Path; Path(%r).write_text(%r)" % (
        str(path),
        f'<testsuites><testsuite tests="{tests}" errors="{errors}" '
        f'failures="{failures}" skipped="{skipped}">'
        + "".join('<testcase name="case" />' for _ in range(tests))
        + "</testsuite></testsuites>",
    )


def test_clean_exit_and_matching_junit_pass_with_receipt(tmp_path: Path) -> None:
    result = _run(tmp_path, _xml(tmp_path))
    assert result["accepted"] is True and result["verdict"] == "PASS"
    assert result["junit"]["tests"] == 1 and result["junit"]["sha256"]
    assert result["process_exit_code"] == 0
    assert (tmp_path / "RING.json").exists()


@pytest.mark.parametrize(
    "code,reason",
    [
        ("pass", "INVALID_JUNIT"),
        ("import sys; sys.exit(1)", "PROCESS_FAILED"),
    ],
)
def test_clean_junit_is_not_inferred_from_process_exit(
    tmp_path: Path, code: str, reason: str
) -> None:
    result = _run(tmp_path, code)
    assert result["verdict"] == reason and result["accepted"] is False


def test_zero_tests_fails_even_when_exit_is_zero(tmp_path: Path) -> None:
    result = _run(tmp_path, _xml(tmp_path, tests=0))
    assert result["verdict"] == "INVALID_JUNIT"


def test_green_junit_does_not_override_nonzero_exit(tmp_path: Path) -> None:
    result = _run(tmp_path, _xml(tmp_path) + "; raise SystemExit(1)")
    assert result["verdict"] == "PROCESS_FAILED" and result["junit"]["valid"]


@pytest.mark.parametrize("key", ["failures", "errors", "skipped"])
def test_red_or_skipped_junit_with_zero_exit_is_rejected(
    tmp_path: Path, key: str
) -> None:
    result = _run(tmp_path, _xml(tmp_path, **{key: 1}))
    assert not result["accepted"] and result["verdict"] == "TESTS_NOT_CLEAN"


def test_deadline_kills_own_process_group_and_keeps_receipt(tmp_path: Path) -> None:
    code = textwrap.dedent("""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        print(child.pid, flush=True)
        time.sleep(60)
    """)
    result = _run(tmp_path, code, timeout=1)
    assert result["verdict"] == "TIMEOUT" and result["timed_out"] is True
    assert result["elapsed_seconds"] < 8
    assert (tmp_path / "RING.json").is_file()
    # Child processes may briefly be zombies, but may never remain live.
    assert supervisor._live_group_members(result["process_group_id"]) == []


def test_process_parent_exits_but_leftover_child_invalidates_green_junit(
    tmp_path: Path,
) -> None:
    code = (
        textwrap.dedent("""
        import subprocess, sys
        from pathlib import Path
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        print(child.pid, flush=True)
    """)
        + "\n"
        + _xml(tmp_path)
    )
    result = _run(tmp_path, code, timeout=4)
    assert result["verdict"] == "LEFTOVER_PROCESSES"
    assert result["lingering_processes_before_teardown"]
    assert not result["accepted"]


def test_short_lived_forkserver_like_child_is_drained_not_flaky(tmp_path: Path) -> None:
    code = (
        textwrap.dedent("""
        import subprocess, sys
        from pathlib import Path
        subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(0.25)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    """)
        + "\n"
        + _xml(tmp_path)
    )
    result = _run(tmp_path, code, timeout=4)
    assert result["accepted"] and result["verdict"] == "PASS"
    assert not result["lingering_processes_before_teardown"]


def test_process_launch_failure_is_reported_not_silent(tmp_path: Path) -> None:
    result = supervisor.supervise(
        name="RING",
        command=["/not-a-real-executable-b11"],
        directory=tmp_path,
        timeout_seconds=2,
    )
    assert result["verdict"] == "START_FAILED"
    assert not result["accepted"] and (tmp_path / "RING.json").is_file()


def test_source_witness_preserves_easy_start_and_external_gate(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(WITNESS, root / "scripts/qualify_b11_development_wsl.sh")
    (root / "README.md").write_text("source\n")
    help_result = subprocess.run(
        ["bash", str(root / "scripts/qualify_b11_development_wsl.sh"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    assert "No external manifest is needed" in help_result.stdout
    makefile = (ROOT / "Makefile").read_text()
    assert "scripts/qualify_b12_development_wsl.sh" in makefile
    content = WITNESS.read_text()
    assert "oh_goon.repository_tooling.b11_ring_supervisor" in content
    assert "external_issuer=NOT_PROVEN" in content
    assert "physical_lace=NOT_PROVEN" in content
    assert "release_authority=NONE" in content


def test_non_wsl_still_fails_before_evidence_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    non_wsl_grep: Path,
) -> None:
    monkeypatch.setenv("PATH", str(non_wsl_grep) + os.pathsep + os.environ["PATH"])
    root = tmp_path / "candidate"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(WITNESS, root / "scripts/qualify_b11_development_wsl.sh")
    dst = tmp_path / "native-evidence"
    result = subprocess.run(
        ["bash", str(root / "scripts/qualify_b11_development_wsl.sh"), str(dst)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 3 and not dst.exists()
    assert "native WSL is required" in result.stderr
