from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/development_check.sh"
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def test_make_check_is_fast_logged_frontdoor_and_certify_is_unchanged_authority() -> (
    None
):
    assert "DEVELOPMENT_CHECK_RUNNER = bash scripts/development_check.sh" in MAKEFILE
    assert (
        'check:\n\t@OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh check $(DEVELOPMENT_CHECK_RUNNER)'
        in MAKEFILE
    )
    assert "certify:" in MAKEFILE
    assert "CERTIFICATION INITIALIZATION REQUIRED" in MAKEFILE
    assert 'test -x "$(CERTIFICATION_RUNNER)"' in MAKEFILE
    assert "\t@$(CERTIFICATION_FRONTDOOR)" in MAKEFILE
    assert "certify: goon-static-admission" not in MAKEFILE


def test_fast_check_does_not_run_complete_or_browser_certification_targets() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "test-python-complete" not in text
    assert "test-tooling-complete" not in text
    assert "browser-targeted-certification" not in text
    assert "browser-required-certification" not in text
    assert "certification-setup" not in text
    assert "test-python" in text
    assert "test-tooling" in text


def test_fast_check_runs_normal_owners_without_local_checkpoint_history(
    tmp_path: Path,
) -> None:
    """An ordinary checkout must not fail because archive-only inputs are absent."""
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    shutil.copy2(ROOT / "scripts/command_banner.sh", scripts / "command_banner.sh")
    calls = tmp_path / "calls"
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'printf \'%s\\n\' "$target" >> "$OH_GOON_TEST_CALLS"\n'
        'case "$target" in phase-evidence-convergence-check|recovery-checkpoint) exit 23 ;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(scripts / SCRIPT.name)],
        cwd=checkout,
        env={**os.environ, "MAKE": str(fake), "OH_GOON_TEST_CALLS": str(calls)},
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    invoked = set(calls.read_text(encoding="utf-8").splitlines())
    assert {
        "test-python",
        "test-tooling",
        "architecture",
        "check-generated",
        "test-frontend",
    } <= invoked
    assert not {"phase-evidence-convergence-check", "recovery-checkpoint"} & invoked


def test_fast_check_reports_multiple_independent_failures(tmp_path: Path) -> None:
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'case "$target" in test-python) exit 4 ;; architecture) exit 5 ;; *) exit 0 ;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env={**os.environ, "MAKE": str(fake)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 1
    assert "Backend fast unit" in completed.stdout and "FAIL" in completed.stdout
    assert "Architecture" in completed.stdout and "FAIL" in completed.stdout
    assert "Frontend unit" in completed.stdout and "PASS" in completed.stdout
    assert "make verify" in completed.stdout


def test_fast_check_lets_backend_and_frontend_operations_report_their_own_failures(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'case "$target" in test-python) exit 23 ;; frontend-typecheck) exit 41 ;; *) exit 0 ;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env={**os.environ, "MAKE": str(fake)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 1
    assert "Backend dependencies" not in completed.stdout
    assert "Backend fast unit" in completed.stdout and "FAIL" in completed.stdout
    assert "Frontend typecheck" in completed.stdout and "FAIL" in completed.stdout
    assert "Frontend unit" in completed.stdout and "PASS" in completed.stdout
    source = SCRIPT.read_text(encoding="utf-8")
    assert "backend-dependencies-check" not in source
    assert "frontend-dependencies-check" not in source
