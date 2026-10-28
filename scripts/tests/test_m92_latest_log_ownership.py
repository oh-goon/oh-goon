from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from run_logging_host_tools import logging_host_env

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_logged.sh"


def _run(env: dict[str, str], marker: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(RUNNER), "test", "bash", "-c", f"printf '{marker}\\n'"),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_latest_log_is_exactly_the_newest_sequential_invocation(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")

    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    first = _run(env, "first-only")
    second = _run(env, "second-only")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    log_dir = tmp_path / "logs" / "test"
    latest = (log_dir / "latest.log").read_text(encoding="utf-8")
    assert "first-only" not in latest
    assert "second-only" in latest
    assert latest.count("OH-GOON RUN LOG") == 1

    completed = [path for path in log_dir.glob("*.log") if path.name != "latest.log"]
    newest = next(
        path for path in completed if "second-only" in path.read_text(encoding="utf-8")
    )
    assert newest.read_bytes() == (log_dir / "latest.log").read_bytes()


def test_superseded_invocation_cannot_mutate_latest_projection(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")

    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    ready = tmp_path / "a-ready"
    release = tmp_path / "a-release"
    os.mkfifo(ready)
    os.mkfifo(release)

    a = subprocess.Popen(
        (
            "bash",
            str(RUNNER),
            "test",
            "bash",
            "-c",
            (
                "printf 'A-before\\n'; "
                f"printf 'ready\\n' > {ready}; "
                f"read _ < {release}; "
                "printf 'A-after\\n'"
            ),
        ),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with ready.open("r", encoding="utf-8") as stream:
            assert stream.readline().strip() == "ready"

        b = subprocess.run(
            ("bash", str(RUNNER), "test", "bash", "-c", "printf 'B-only\\n'"),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert b.returncode == 0, b.stderr

        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        a_stdout, a_stderr = a.communicate(timeout=10)
        assert a.returncode == 0, (a_stdout, a_stderr)
    finally:
        if a.poll() is None:
            a.kill()
            a.wait(timeout=2)

    log_dir = tmp_path / "logs" / "test"
    latest = (log_dir / "latest.log").read_text(encoding="utf-8")
    assert "B-only" in latest
    assert "A-before" not in latest
    assert "A-after" not in latest
    assert latest.count("OH-GOON RUN LOG") == 1

    completed = [
        path.read_text(encoding="utf-8")
        for path in log_dir.glob("*.log")
        if path.name != "latest.log"
    ]
    assert any("A-before" in text and "A-after" in text for text in completed)
    assert any("B-only" in text for text in completed)


def test_latest_publication_failure_does_not_corrupt_run_evidence_or_result(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")

    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    latest = tmp_path / "logs" / "test" / "latest.log"
    latest.mkdir(parents=True)
    latest.parent.chmod(0o700)
    latest.parent.parent.chmod(0o700)

    result = subprocess.run(
        ("bash", str(RUNNER), "test", "bash", "-c", "printf 'owner-only\\n'; exit 7"),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 7
    assert "latest run-log projection could not be published" in result.stderr
    assert "persistent run logging became unavailable" not in result.stderr
    assert "Saved run log:" in result.stdout
    assert "Latest run log:" not in result.stdout
    assert latest.is_dir()

    completed = list((tmp_path / "logs" / "test").glob("[0-9]*.log"))
    assert len(completed) == 1
    evidence = completed[0].read_text(encoding="utf-8")
    assert "owner-only" in evidence
    assert "OH-GOON RUN RESULT: FAIL (exit 7)" in evidence
