"""M209: the optional WSL make qualifier is one diagnostic attempt, never APP admission."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/qualify_native_wsl.sh"
LABELS = [
    "source-transport-clean",
    "sync",
    "frontend-schedule-plan-input-regression",
    "architecture",
    "verify-p0",
    "test",
    "frontend-functional-certification",
    "certify",
]


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    source = tmp_path / "candidate"
    (source / "scripts").mkdir(parents=True)
    (source / "backend").mkdir()
    (source / "frontend").mkdir()
    (source / "Makefile").write_text("# fixture, no tests loaded\n")
    (source / "backend/uv.lock").write_text("backend authoritative lock\n")
    (source / "frontend/package-lock.json").write_text("frontend authoritative lock\n")
    (source / "scripts/qualify_native_issuer_wheel.sh").write_text(
        "#!/bin/sh\nexit 0\n"
    )
    (source / "scripts/qualify_native_wsl.sh").write_bytes(SCRIPT.read_bytes())
    (source / "scripts/classify_native_fleet_blocker.cjs").write_bytes(
        (ROOT / "scripts/classify_native_fleet_blocker.cjs").read_bytes()
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_grep = bin_dir / "grep"
    fake_grep.write_text(
        "#!/bin/bash\n"
        'if [[ "$*" == *"/proc/sys/kernel/osrelease"* ]]; then exit 0; fi\n'
        'exec /usr/bin/grep "$@"\n'
    )
    fake_grep.chmod(0o755)
    make = bin_dir / "make"
    make.write_text(
        """#!/bin/bash
set -euo pipefail
name="${@: -1}"
printf "%s\\n" "$name" >> "$M209_TEST_CALLS"
echo "mock owner: $name"
if [[ "$name" == source-transport-clean && "${M209_TEST_CLEAN_TRANSPORT:-0}" == 1 ]]; then
  find . -type f \\( -name '*:Zone.Identifier' -o -name '*:Zone.Identifier:$DATA' -o -name '*.patch' -o -name '*.patch.sha256' \\) -delete
fi
if [[ "$name" == source-transport-clean && "${M209_TEST_DELETE_AUTHORED:-0}" == 1 ]]; then
  rm -f -- source-example.py
fi
if [[ "$name" == sync && "${M209_TEST_LOCK_DRIFT:-0}" == 1 ]]; then
  echo drift >> backend/uv.lock
fi
if [[ "$name" == architecture && "${M209_TEST_SOURCE_DRIFT:-0}" == 1 ]]; then
  echo tamper >> Makefile
fi
if [[ "$name" == test && "${M209_TEST_GENERATED_CACHE:-0}" == 1 ]]; then
  mkdir -p frontend/dist test-results playwright-report backend/__pycache__
  echo generated > frontend/tsconfig.tsbuildinfo
  echo generated > frontend/dist/bundle.js
  echo generated > test-results/runner.out
  echo generated > playwright-report/index.html
  echo generated > backend/__pycache__/code.pyc
fi
if [[ "$name" == "${M209_TEST_FAIL_STEP:-__none__}" ]]; then exit 17; fi
if [[ "$name" == certify ]]; then
  case "${M209_TEST_CERTIFY:-blocked}" in
    blocked) echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={"schema": "oh-goon.fleet-provisioning-blocker/v1", "reason": "FLEET_ENFORCER_NOT_PROVISIONED", "status": "BLOCKED", "app_pass": false, "certification_goon_started": false, "authority": "NOT_ESTABLISHED", "authenticated_replay": "NOT_AUTHORIZED", "group_a_gate_a": "NOT_RUN"}'; exit 2 ;;
    spoofed) echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={"reason": "FLEET_ENFORCER_NOT_PROVISIONED", "app_pass": true, "certification_goon_started": true}'; exit 2 ;;
    split) echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={broken'; echo 'unrelated "reason": "FLEET_ENFORCER_NOT_PROVISIONED"'; echo 'unrelated "certification_goon_started": false'; echo 'unrelated "app_pass": false'; exit 2 ;;
    duplicate) echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={"schema":"oh-goon.fleet-provisioning-blocker/v1","reason":"FLEET_ENFORCER_NOT_PROVISIONED","status":"BLOCKED","app_pass":false,"certification_goon_started":false,"authority":"NOT_ESTABLISHED","authenticated_replay":"NOT_AUTHORIZED","group_a_gate_a":"NOT_RUN"}'; echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={"schema":"oh-goon.fleet-provisioning-blocker/v1","reason":"FLEET_ENFORCER_NOT_PROVISIONED","status":"BLOCKED","app_pass":false,"certification_goon_started":false,"authority":"NOT_ESTABLISHED","authenticated_replay":"NOT_AUTHORIZED","group_a_gate_a":"NOT_RUN"}'; exit 2 ;;
    wrong_schema) echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={"schema":"oh-goon.not-the-blocker/v1","reason":"FLEET_ENFORCER_NOT_PROVISIONED","status":"BLOCKED","app_pass":false,"certification_goon_started":false,"authority":"NOT_ESTABLISHED","authenticated_replay":"NOT_AUTHORIZED","group_a_gate_a":"NOT_RUN"}'; exit 2 ;;
    malformed) echo 'OH_GOON_FLEET_PROVISIONING_BLOCKER={"reason": "FLEET_ENFORCER_NOT_PROVISIONED", "app_pass": false, "certification_goon_started": false,}'; exit 2 ;;
    success) exit 0 ;;
  esac
fi
"""
    )
    make.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["M209_TEST_CALLS"] = str(tmp_path / "calls.txt")
    return source, env


def _run(
    source: Path, env: dict[str, str], evidence: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/qualify_native_wsl.sh", str(evidence)],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )


def _calls(env: dict[str, str]) -> list[str]:
    path = Path(env["M209_TEST_CALLS"])
    return path.read_text().splitlines() if path.exists() else []


def _post(evidence: Path) -> str:
    return (evidence / "M209_POST_RUN_PROVENANCE.txt").read_text()


def test_wsl_guard_rejects_unqualified_linux_before_work(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    # Do not run this test on WSL: the intended guard is genuinely environment-bound.
    if "microsoft" in os.uname().release.lower() or "wsl" in os.uname().release.lower():
        pytest.skip("this negative guard fixture requires non-WSL host")
    # Use the system grep, bypassing the fixture's WSL-only mock.
    env["PATH"] = os.environ["PATH"]
    result = _run(source, env, tmp_path / "proof")
    assert result.returncode == 3
    assert "WSL kernel required" in result.stderr
    assert not (tmp_path / "proof").exists()
    assert not _calls(env)


def test_first_failing_owner_stops_and_writes_post_receipt(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    env["M209_TEST_FAIL_STEP"] = "verify-p0"
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 17, result.stdout + result.stderr
    assert _calls(env) == [
        "source-transport-clean",
        "sync",
        "frontend-schedule-plan-input-regression",
        "architecture",
        "verify-p0",
    ]
    assert not (evidence / "06_test.log").exists()
    post = _post(evidence)
    assert "last_step=05_verify_p0" in post
    assert "last_step_exit=17" in post
    assert "canonical_members_integrity_same_attempt=PASS" in post
    assert "locks_byte_identical_same_attempt=PASS" in post
    assert "app_pass=NOT_ESTABLISHED" in post
    assert "certify_classification=NOT_RUN" in post
    assert (
        "05_verify_p0\t17\tFAILED_OR_BLOCKED"
        in (evidence / "M209_STEPS.tsv").read_text()
    )


def test_all_seven_pre_certification_owners_and_expected_blocker(
    tmp_path: Path,
) -> None:
    source, env = _fixture(tmp_path)
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 2, result.stdout + result.stderr
    assert _calls(env) == LABELS[:3] + [
        "architecture",
        "verify-p0",
        "test",
        "frontend-functional-certification",
        "certify",
    ]
    for i in range(1, 9):
        assert len(list(evidence.glob(f"{i:02d}_*.log"))) == 1
    assert (evidence / "02b_schedule_plan_inputs.log").is_file()
    assert (
        "02b_schedule_plan_inputs\t0\tPASS_COMMAND_ONLY"
        in (evidence / "M209_STEPS.tsv").read_text()
    )
    post = _post(evidence)
    assert "qualification_phase=BLOCKED_AT_DEPLOYMENT_ENROLLMENT" in post
    assert (
        "certify_classification=EXPECTED_PROVISIONING_BLOCKER_OBSERVED_NOT_APP_PASS"
        in post
    )
    assert "canonical_members_integrity_same_attempt=PASS" in post
    assert "locks_byte_identical_same_attempt=PASS" in post
    assert "pre_run_source_manifest_sha256=" in post
    assert "post_run_source_manifest_sha256=" in post
    assert "app_pass=NOT_ESTABLISHED" in post


def test_similar_text_must_not_qualify_as_a_fleet_blocker(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    env["M209_TEST_CERTIFY"] = "spoofed"
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 2
    assert "certify_classification=UNEXPECTED_CERTIFY_FAILURE" in _post(evidence)


def test_even_certify_exit_zero_does_not_authorize_app_pass(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    env["M209_TEST_CERTIFY"] = "success"
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 2
    assert "certify_classification=COMMAND_EXIT_0_APP_PROOF_UNVERIFIED" in _post(
        evidence
    )
    assert "app_pass=NOT_ESTABLISHED" in _post(evidence)


def test_sync_lock_drift_fails_before_wheel(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    env["M209_TEST_LOCK_DRIFT"] = "1"
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 5
    assert _calls(env) == ["source-transport-clean", "sync"]
    assert "qualification_phase=STOPPED_AT_LOCK_DRIFT" in _post(evidence)
    assert "locks_byte_identical_same_attempt=FAIL" in _post(evidence)
    assert "canonical_members_integrity_same_attempt=FAIL" in _post(evidence)


def test_source_drift_is_never_reported_as_pass(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    env["M209_TEST_SOURCE_DRIFT"] = "1"
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 5
    assert _calls(env) == [
        "source-transport-clean",
        "sync",
        "frontend-schedule-plan-input-regression",
        "architecture",
    ]
    assert "qualification_phase=STOPPED_AT_SOURCE_DRIFT" in _post(evidence)
    assert "canonical_members_integrity_same_attempt=FAIL" in _post(evidence)
    assert "app_pass=NOT_ESTABLISHED" in _post(evidence)


@pytest.mark.parametrize("inside", [True, False])
def test_rejects_unsafe_evidence_destination_before_commands(
    tmp_path: Path, inside: bool
) -> None:
    source, env = _fixture(tmp_path)
    evidence = source / "proof" if inside else tmp_path / "proof"
    if not inside:
        evidence.mkdir()
        (evidence / "do-not-overwrite").write_text("preserved")
    result = _run(source, env, evidence)
    assert result.returncode == 3
    assert not _calls(env)
    if not inside:
        assert (evidence / "do-not-overwrite").read_text() == "preserved"
    else:
        assert not evidence.exists()


def test_new_make_target_is_explicitly_diagnostic_and_does_not_change_release_authority() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text()
    assert "qualify-native-wsl:" in makefile
    assert '@bash scripts/qualify_native_wsl.sh "$(QUALIFY_EVIDENCE_DIR)"' in makefile
    assert "QUALIFY_EVIDENCE_DIR ?= ../OH_GOON_NATIVE_EVIDENCE" in makefile
    assert "CERTIFICATION_FRONTDOOR = OH_GOON_RUN_LOG_DIR=" in makefile
    assert (
        "native-wsl"
        not in (ROOT / "templates/plans/oh-goon/prepare-release/plan.yml").read_text()
    )


@pytest.mark.parametrize(
    "added_sidecars",
    [
        [
            "source-example.py:Zone.Identifier",
            "frontend/package.json:Zone.Identifier:$DATA",
        ],
        ["delivery.patch", "delivery.patch.sha256"],
        [
            "source-example.py:Zone.Identifier",
            "delivery.patch",
            "delivery.patch.sha256",
        ],
    ],
)
def test_m210_transport_cleanup_is_not_authored_source_drift(
    tmp_path: Path, added_sidecars: list[str]
) -> None:
    """Windows-extracted source must survive its expected first cleanup step."""
    source, env = _fixture(tmp_path)
    (source / "source-example.py").write_text("canonical = 1\n")
    for name in added_sidecars:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("transport only\n")
    env["M209_TEST_CLEAN_TRANSPORT"] = "1"
    proof = tmp_path / "proof"
    result = _run(source, env, proof)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "STOPPED_AT_SOURCE_DRIFT" not in result.stdout + result.stderr
    assert "canonical_members_integrity_same_attempt=PASS" in _post(proof)
    assert "locks_byte_identical_same_attempt=PASS" in _post(proof)
    assert _calls(env) == LABELS[:3] + [
        "architecture",
        "verify-p0",
        "test",
        "frontend-functional-certification",
        "certify",
    ]
    for name in added_sidecars:
        assert not (source / name).exists()
        assert name not in (proof / "M209_PRE_RUN_SOURCE.sha256").read_text()
    assert "source-example.py" in (proof / "M209_PRE_RUN_SOURCE.sha256").read_text()


def test_m210_transport_cleanup_cannot_mask_removal_of_an_authored_file(
    tmp_path: Path,
) -> None:
    source, env = _fixture(tmp_path)
    (source / "source-example.py").write_text("canonical = 1\n")
    (source / "source-example.py:Zone.Identifier").write_text("transport only\n")
    env["M209_TEST_CLEAN_TRANSPORT"] = "1"
    env["M209_TEST_DELETE_AUTHORED"] = "1"
    result = _run(source, env, tmp_path / "proof")
    assert result.returncode == 5, result.stdout + result.stderr
    assert _calls(env) == ["source-transport-clean"]
    assert "canonical_members_integrity_same_attempt=FAIL" in _post(tmp_path / "proof")
    assert "qualification_phase=STOPPED_AT_SOURCE_DRIFT" in _post(tmp_path / "proof")


def test_m210_exact_transport_name_exclusions_match_the_python_owner(
    tmp_path: Path,
) -> None:
    """The tiny bash filter must not silently grow beyond the source policy owner."""
    from oh_goon.core.source_transport_metadata import (
        is_disposable_source_transport_artifact_name,
    )

    source, env = _fixture(tmp_path)
    names = [
        "x:Zone.Identifier",
        "x:Zone.Identifier:$DATA",
        "x.patch",
        "x.patch.sha256",
        ":Zone.Identifier",
        ":Zone.Identifier:$DATA",
        ".patch",
        ".patch.sha256",
        "x.patch.backup",
        "x:Zone.Identifier.more",
        "x.patch.sha256.old",
        "normal.md",
    ]
    for name in names:
        (source / name).write_text("content\n")
    env["M209_TEST_FAIL_STEP"] = "sync"  # still capture an admitted pre/post snapshot
    proof = tmp_path / "proof"
    result = _run(source, env, proof)
    assert result.returncode == 17, result.stdout + result.stderr
    seen = (proof / "M209_PRE_RUN_SOURCE.sha256").read_text()
    for name in names:
        included = any(line.endswith(f"  ./{name}") for line in seen.splitlines())
        assert included is not is_disposable_source_transport_artifact_name(name), name


def test_m210_generated_materializations_are_not_source_drift(tmp_path: Path) -> None:
    source, env = _fixture(tmp_path)
    env["M209_TEST_GENERATED_CACHE"] = "1"
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "canonical_members_integrity_same_attempt=PASS" in _post(evidence)
    assert "qualification_phase=BLOCKED_AT_DEPLOYMENT_ENROLLMENT" in _post(evidence)
    assert _calls(env)[-1] == "certify"
    for relative in [
        "frontend/tsconfig.tsbuildinfo",
        "frontend/dist/bundle.js",
        "test-results/runner.out",
    ]:
        assert relative not in (evidence / "M209_POST_RUN_SOURCE.sha256").read_text()


def test_m215_schedule_regression_failure_stops_before_expensive_owners_and_keeps_evidence(
    tmp_path: Path,
) -> None:
    """Native Schedule regression must fail before wheel/P0/test/certify, with same-run POST."""
    source, env = _fixture(tmp_path)
    env["M209_TEST_FAIL_STEP"] = "frontend-schedule-plan-input-regression"
    evidence = tmp_path / "evidence"
    result = _run(source, env, evidence)
    assert result.returncode == 17, result.stdout + result.stderr
    assert _calls(env) == [
        "source-transport-clean",
        "sync",
        "frontend-schedule-plan-input-regression",
    ]
    assert (evidence / "02b_schedule_plan_inputs.log").is_file()
    assert not (evidence / "03_native_issuer_wheel.log").exists()
    assert not (evidence / "05_verify_p0.log").exists()
    assert not (evidence / "06_test.log").exists()
    assert not (evidence / "08_certify.log").exists()
    post = _post(evidence)
    assert "qualification_phase=STOPPED_AT_02b_schedule_plan_inputs" in post
    assert "last_step=02b_schedule_plan_inputs" in post
    assert "last_step_exit=17" in post
    assert "canonical_members_integrity_same_attempt=PASS" in post
    assert "locks_byte_identical_same_attempt=PASS" in post
    assert "certify_classification=NOT_RUN" in post
    assert "app_pass=NOT_ESTABLISHED" in post
    assert (
        "02b_schedule_plan_inputs\t17\tFAILED_OR_BLOCKED"
        in (evidence / "M209_STEPS.tsv").read_text()
    )


def test_m215_schedule_frontend_qualification_is_original_locked_vitest_only() -> None:
    """No separate executor, unpinned npx or change to release certification semantics."""
    makefile = (ROOT / "Makefile").read_text()
    runner = SCRIPT.read_text()
    assert "frontend-schedule-plan-input-regression:" in makefile
    assert (
        "npm run test -- src/features/schedules/components/ScheduleCreatePanel.test.tsx src/features/schedules/components/schedulePlanInputDraft.test.ts"
        in makefile
    )
    assert "run_step 02_sync make --no-print-directory sync\n" in runner
    assert (
        "run_step 02b_schedule_plan_inputs make --no-print-directory frontend-schedule-plan-input-regression\n"
        in runner
    )
    assert (
        runner.index("run_step 02_sync ")
        < runner.index("run_step 02b_schedule_plan_inputs ")
        < runner.index("run_step 03_native_issuer_wheel ")
    )
    assert runner.index("run_step 02b_schedule_plan_inputs ") < runner.index(
        "run_step 06_test "
    )
    assert (
        "npx"
        not in makefile.split("frontend-schedule-plan-input-regression:", 1)[1].split(
            "\n\n", 1
        )[0]
    )
    assert "make --no-print-directory certify" in runner
    assert "app_pass=NOT_ESTABLISHED" in runner


@pytest.mark.parametrize("case", ["split", "duplicate", "wrong_schema", "malformed"])
def test_m216_certify_blocker_must_be_one_valid_complete_receipt(
    tmp_path: Path, case: str
) -> None:
    """Fragmented, duplicated, mislabeled or malformed output is not an observed blocker."""
    source, env = _fixture(tmp_path)
    env["M209_TEST_CERTIFY"] = case
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 2, result.stdout + result.stderr
    assert _calls(env)[-1] == "certify"
    assert "certify_classification=UNEXPECTED_CERTIFY_FAILURE" in _post(evidence)
    assert "qualification_phase=STOPPED_AT_CERTIFY" in _post(evidence)
    assert "app_pass=NOT_ESTABLISHED" in _post(evidence)
    assert (
        "08_certify\t2\tUNEXPECTED_FAILURE" in (evidence / "M209_STEPS.tsv").read_text()
    )


def test_a1d_untracked_source_symlink_rejected_before_any_make_owner(
    tmp_path: Path,
) -> None:
    source, env = _fixture(tmp_path)
    (source / "untracked-link").symlink_to("Makefile")
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 5, result.stdout + result.stderr
    assert "untracked symlink or special source entry" in result.stderr
    assert not _calls(env)
    assert "qualification_phase=PRE_RUN_SOURCE_SNAPSHOT_FAILED" in _post(evidence)


def test_a1d_untracked_source_fifo_rejected_before_any_make_owner(
    tmp_path: Path,
) -> None:
    source, env = _fixture(tmp_path)
    os.mkfifo(source / "untracked-fifo")
    evidence = tmp_path / "proof"
    result = _run(source, env, evidence)
    assert result.returncode == 5, result.stdout + result.stderr
    assert not _calls(env)
    assert "qualification_phase=PRE_RUN_SOURCE_SNAPSHOT_FAILED" in _post(evidence)
