from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/development_test.sh"


def _fake_environment(
    tmp_path: Path,
    *,
    failing_targets: set[str] | None = None,
    diagnostic_blocked: bool = False,
    wrapped_diagnostic: bool = False,
    host_pytest_available: bool = True,
    backend_capability_ready: bool = True,
) -> tuple[dict[str, str], Path]:
    if os.name == "nt":
        pytest.skip("development test runner is POSIX shell tooling")
    failing_targets = failing_targets or set()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"

    fake_make = bin_dir / "fake-make"
    fake_make.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "target=\"${@: -1}\"\n"
        "printf '%s\\n' \"$target\" >> \"$OH_GOON_TEST_CALLS\"\n"
        "if [[ \"${OH_GOON_DIAGNOSTIC_BLOCKED:-0}\" == 1 && \"$target\" == test-python-core-diagnostic ]]; then\n"
        "  if [[ \"${OH_GOON_WRAPPED_DIAGNOSTIC:-0}\" == 1 ]]; then\n"
        "    mkdir -p \"$OH_GOON_RUN_LOG_DIR/test\"; printf '3\\n' > \"$OH_GOON_RUN_LOG_DIR/test/hosted-diagnostic-exit.status\"; exit 2; fi\n"
        "  exit 3; fi\n"
        "if [[ \"${OH_GOON_BACKEND_CAPABILITY_READY:-1}\" == 0 && \"$target\" == backend-dependencies-check ]]; then exit 8; fi\n"
        "case \" $OH_GOON_FAIL_TARGETS \" in\n"
        "  *\" $target \"*) exit 9 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o755)

    for name in ("uv", "node", "npm"):
        tool = bin_dir / name
        tool.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    python3 = bin_dir / "python3"
    python3.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${OH_GOON_HOST_PYTEST_MISSING:-0}\" == 1 ]]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["PYTHON"] = str(python3)
    env["MAKE"] = str(fake_make)
    env["OH_GOON_TEST_CALLS"] = str(calls)
    env["OH_GOON_FAIL_TARGETS"] = " ".join(sorted(failing_targets))
    env["OH_GOON_DIAGNOSTIC_BLOCKED"] = "1" if diagnostic_blocked else "0"
    env["OH_GOON_WRAPPED_DIAGNOSTIC"] = "1" if wrapped_diagnostic else "0"
    env["OH_GOON_HOST_PYTEST_MISSING"] = "0" if host_pytest_available else "1"
    env["OH_GOON_BACKEND_CAPABILITY_READY"] = "1" if backend_capability_ready else "0"
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    return env, calls


def _run(
    tmp_path: Path,
    *,
    failing_targets: set[str] | None = None,
    diagnostic_blocked: bool = False,
    wrapped_diagnostic: bool = False,
    constrained_host: bool = False,
    host_pytest_available: bool = True,
    backend_capability_ready: bool | None = None,
    run_hosted_diagnostic: bool = False,
    diagnostic_option: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    if backend_capability_ready is None:
        backend_capability_ready = not constrained_host
    env, calls = _fake_environment(
        tmp_path,
        failing_targets=failing_targets,
        diagnostic_blocked=diagnostic_blocked,
        wrapped_diagnostic=wrapped_diagnostic,
        host_pytest_available=host_pytest_available,
        backend_capability_ready=backend_capability_ready,
    )
    env["OH_GOON_CONSTRAINED_HOST"] = "1" if constrained_host else "0"
    env["OH_GOON_RUN_HOSTED_DIAGNOSTIC"] = (
        diagnostic_option
        if diagnostic_option is not None
        else ("1" if run_hosted_diagnostic else "0")
    )
    source_layout = ROOT / "backend/.venv/bin/oh-goon-source-layout"
    created_source_layout = not source_layout.exists()
    if created_source_layout:
        source_layout.parent.mkdir(parents=True, exist_ok=True)
        source_layout.write_text(
            "#!/usr/bin/env bash\nprintf '%s\n' sha256:test-development-runner\n",
            encoding="utf-8",
        )
        source_layout.chmod(0o755)
    try:
        result = subprocess.run(
            ("bash", str(RUNNER)), cwd=ROOT, env=env, capture_output=True, text=True
        )
    finally:
        if created_source_layout:
            source_layout.unlink(missing_ok=True)
    invoked = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, invoked


def _all_native_checks() -> list[str]:
    return [
        "test-python",
        "test-tooling",
        "architecture",
        "check-generated",
        "test-integration",
        "frontend-typecheck",
        "test-frontend",
        "frontend-architecture",
        "i18n-check",
        "frontend-build",
    ]


def test_development_runner_runs_actual_native_checks_without_readiness_oracles(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path)
    assert result.returncode == 0
    assert invoked == _all_native_checks()
    assert "Host-only diagnostic     SKIPPED" in result.stdout
    assert "make test uses authoritative native uv/.venv tests" in result.stdout
    assert "qualification-preflight" not in invoked
    assert "frontend-dependencies-check" not in invoked
    assert "RESULT: PASSED" in result.stdout


def test_native_check_failures_do_not_stop_independent_checks(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path, failing_targets={"test-python", "frontend-typecheck"}
    )
    assert result.returncode == 1
    assert invoked == _all_native_checks()
    assert "Backend fast unit        FAIL" in result.stdout
    assert "Host-only diagnostic     SKIPPED" in result.stdout
    assert "Frontend typecheck       FAIL" in result.stdout
    assert "Frontend unit            PASS" in result.stdout
    assert "Frontend build           PASS" in result.stdout


def test_normal_development_does_not_use_backend_readiness_oracle(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path, backend_capability_ready=False)
    assert result.returncode == 0
    assert invoked == _all_native_checks()
    assert "backend-dependencies-check" not in invoked
    assert "Backend dependencies" not in result.stdout


def test_frontend_failure_is_native_and_does_not_create_dependency_classifier(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path, failing_targets={"frontend-typecheck"})
    assert result.returncode == 1
    assert "frontend-typecheck" in invoked
    assert "test-frontend" in invoked
    assert "frontend-architecture" in invoked
    assert "Frontend typecheck       FAIL" in result.stdout
    assert "Frontend dependencies" not in result.stdout


def test_frontend_failure_guard_checks_source_integrity_only() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    guard = text.split("frontend_failure_guard() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_workspace_integrity" in guard
    assert "frontend-dependencies-check" not in guard
    assert "node_modules" not in guard
    assert "npm ls" not in guard


def test_development_runner_never_acquires_dependencies() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    for forbidden in ("frontend-install", "make init", "uv sync", "npm ci", "npm install"):
        assert forbidden not in text


def test_fast_development_path_does_not_run_quality_browser_or_certification_gates(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path)
    assert result.returncode == 0
    forbidden = {
        "lint",
        "format",
        "format-check",
        "backend-check",
        "frontend-check",
        "tooling-check",
        "browser-test",
        "browser-required-certification",
        "certify",
        "verify-p1",
        "local-runtime-verification",
        "strict-local-runtime-acceptance",
        "test-real-world",
    }
    assert forbidden.isdisjoint(invoked)


def test_predependency_diagnostic_delegates_required_capability_admission() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'source "$ROOT/scripts/host_python.sh"' in text
    assert 'HOST_PYTHON="$(select_host_python)"' in text
    assert 'command -v "$HOST_PYTHON"' in text
    assert '"$HOST_PYTHON" -c \'import pytest\'' not in text
    assert 'run_optional_diagnostic "Host-only diagnostic" test-python-core-diagnostic' in text


def test_native_default_never_probes_or_invokes_separate_host_diagnostic(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path, failing_targets={"test-python-core-diagnostic"}, host_pytest_available=False
    )
    assert result.returncode == 0
    assert invoked == _all_native_checks()
    assert "Host-only diagnostic     SKIPPED" in result.stdout
    assert "Pre-dependency diagnostic" not in result.stdout


def test_explicit_host_diagnostic_still_runs_without_replacing_native_checks(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path, run_hosted_diagnostic=True)
    assert result.returncode == 0
    assert invoked == ["test-python-core-diagnostic", *_all_native_checks()]
    assert "Host-only diagnostic     PASS" in result.stdout
    assert "RESULT: PASSED" in result.stdout


def test_hosted_pass_cannot_mask_native_backend_failure(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path,
        run_hosted_diagnostic=True,
        failing_targets={"test-python"},
    )
    assert result.returncode == 1
    assert invoked == ["test-python-core-diagnostic", *_all_native_checks()]
    assert "Host-only diagnostic     PASS" in result.stdout
    assert "Backend fast unit        FAIL" in result.stdout
    assert "RESULT: FAILED" in result.stdout


def test_explicit_host_unavailability_is_blocked_not_reported_as_skipped(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path, run_hosted_diagnostic=True, diagnostic_blocked=True,
        wrapped_diagnostic=True,
    )
    assert result.returncode == 1
    assert invoked == ["test-python-core-diagnostic", *_all_native_checks()]
    assert "Host-only diagnostic     BLOCKED" in result.stdout
    assert "requested host diagnostic unavailable" in result.stdout
    assert "RESULT: FAILED" in result.stdout


def test_invalid_host_diagnostic_flag_fails_before_running_checks(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path, diagnostic_option="unexpected")
    assert result.returncode == 2
    assert invoked == []
    assert "OH_GOON_RUN_HOSTED_DIAGNOSTIC must be 0 or 1" in result.stderr


def test_constrained_host_uses_existing_backend_materialization_without_acquisition(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path, constrained_host=True, backend_capability_ready=True
    )
    assert result.returncode == 0
    assert invoked == ["test-python-core-diagnostic", "backend-dependencies-check", *_all_native_checks()]
    assert "existing local native backend capability detected" in result.stdout
    assert "RESULT: HOSTED-DIAGNOSTIC-PASSED" in result.stdout


def test_constrained_host_without_backend_still_runs_native_frontend_checks(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path, constrained_host=True, backend_capability_ready=False)
    assert result.returncode == 0
    assert invoked == [
        "test-python-core-diagnostic",
        "backend-dependencies-check",
        "frontend-typecheck",
        "test-frontend",
        "frontend-architecture",
        "i18n-check",
        "frontend-build",
    ]
    assert "Backend dependencies     SKIPPED" in result.stdout
    assert "RESULT: HOSTED-DIAGNOSTIC-PASSED" in result.stdout


def test_constrained_host_cannot_hide_diagnostic_failure(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path,
        constrained_host=True,
        backend_capability_ready=False,
        failing_targets={"test-python-core-diagnostic"},
    )
    assert "frontend-typecheck" in invoked
    assert result.returncode == 1
    assert "Host-only diagnostic     FAIL" in result.stdout
    assert "RESULT: HOSTED-DIAGNOSTIC-PASSED" not in result.stdout


def test_constrained_host_cannot_report_success_when_required_host_capability_is_missing(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path,
        constrained_host=True,
        diagnostic_blocked=True,
        backend_capability_ready=False,
    )
    assert "backend-dependencies-check" in invoked
    assert "frontend-typecheck" in invoked
    assert result.returncode == 1
    assert "requested host diagnostic unavailable; native backend checks are separate evidence" in result.stdout
    assert "RESULT: HOSTED-DIAGNOSTIC-PASSED" not in result.stdout


def test_make_wrapped_host_unavailability_does_not_affect_default_native_development(tmp_path: Path) -> None:
    result, invoked = _run(tmp_path, diagnostic_blocked=True, wrapped_diagnostic=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert invoked == _all_native_checks()
    assert "Host-only diagnostic     SKIPPED" in result.stdout
    assert "RESULT: PASSED" in result.stdout


def test_make_wrapped_host_unavailability_blocks_constrained_mode(tmp_path: Path) -> None:
    result, _ = _run(
        tmp_path, diagnostic_blocked=True, wrapped_diagnostic=True,
        constrained_host=True, backend_capability_ready=False,
    )
    assert result.returncode == 1
    assert "Host-only diagnostic     BLOCKED" in result.stdout


def test_missing_constrained_host_diagnostic_blocks_even_with_native_backend_ready(tmp_path: Path) -> None:
    result, invoked = _run(
        tmp_path,
        constrained_host=True,
        backend_capability_ready=True,
        diagnostic_blocked=True,
        wrapped_diagnostic=True,
    )
    assert result.returncode == 1
    assert invoked == ["test-python-core-diagnostic", "backend-dependencies-check", *_all_native_checks()]
    assert "Host-only diagnostic     BLOCKED" in result.stdout
    assert "Backend fast unit        PASS" in result.stdout
    assert "RESULT: FAILED" in result.stdout


def test_genuine_diagnostic_failure_must_not_be_skipped(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, failing_targets={"test-python-core-diagnostic"}, run_hosted_diagnostic=True)
    assert result.returncode == 1
    assert "Host-only diagnostic     FAIL" in result.stdout
