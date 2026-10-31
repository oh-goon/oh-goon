from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from oh_goon.repository_tooling import daily_regression as daily
from oh_goon.repository_tooling.pytest_execution_membership import (
    MEMBERSHIP_SCHEMA,
    ExecutionMembershipReceipt,
    ExecutionMembershipStore,
)
from oh_goon.repository_tooling.pytest_execution_report import execution_report_schema
from oh_goon.repository_tooling.pytest_member_selection import collect_native_pytest
from oh_goon.repository_tooling.pytest_process_environment import (
    resolve_pytest_invocation,
)

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
SLOT = "2026-10-30"


def _native(
    member: str = "test_case.py::test_behavior", *, outcome: str = "passed"
) -> dict[str, Any]:
    receipt = ExecutionMembershipReceipt(
        schema=MEMBERSHIP_SCHEMA,
        suite_identity="sha256:" + "b" * 64,
        execution_plan_identity="sha256:" + "c" * 64,
        execution_run_identity="sha256:" + "d" * 64,
        selected_membership_identity="sha256:" + "e" * 64,
        terminal_membership_identity="sha256:" + "e" * 64,
        selected=(member,),
        attempted=(member,),
        completed=(member,),
        passed=(member,) if outcome == "passed" else (),
        failed=(member,) if outcome == "failed" else (),
        non_passed=(member,) if outcome == "non_passed" else (),
        resumed=(),
        missing=(),
        duplicates=(),
        extras=(),
        attempt_records=(
            {
                "member": member,
                "status": "PASSED"
                if outcome == "passed"
                else "FAILED"
                if outcome == "failed"
                else "NON_PASSED",
                "pytest_outcomes": [
                    {
                        "nodeid": member,
                        "outcome": "PASSED"
                        if outcome == "passed"
                        else "FAILED"
                        if outcome == "failed"
                        else "SKIPPED",
                    }
                ],
            },
        ),
        retry_bisection_lineage=(),
        terminal_success=outcome == "passed",
        effective_pytest_outcomes=(
            {
                "member": member,
                "status": "PASSED"
                if outcome == "passed"
                else "FAILED"
                if outcome == "failed"
                else "NON_PASSED",
                "pytest_outcomes": [
                    {
                        "nodeid": member,
                        "outcome": "PASSED"
                        if outcome == "passed"
                        else "FAILED"
                        if outcome == "failed"
                        else "SKIPPED",
                    }
                ],
            },
        ),
    )
    return cast(
        dict[str, Any],
        json.loads(
            json.dumps(
                {
                    "schema": execution_report_schema(),
                    "granularity": "tests",
                    "marker": "not certification",
                    "status": "FAIL" if outcome == "failed" else "PASS",
                    "suite_timed_out": False,
                    "resume_enabled": False,
                    "reused_member_count": 0,
                    "source_identity": "sha256:" + "f" * 64,
                    "membership_receipt": receipt.as_dict(),
                    "native_collection": {
                        "root": ".",
                        "discovered": [member],
                        "deselected": [],
                    },
                }
            )
        ),
    )


def _owner(
    target: str, *, report: dict[str, Any] | None = None, code: int = 0
) -> dict[str, Any]:
    return daily.owner_result(
        target=target,
        sha=SHA,
        slot=SLOT,
        run_id=11,
        attempt=1,
        exit_code=code,
        native_report=report,
        requires_native_report=report is not None,
    )


def _aggregate(
    targets: list[str], rows: list[dict[str, Any]], job_result: str = "success"
) -> dict[str, Any]:
    return daily.aggregate_results(
        targets,
        rows,
        sha=SHA,
        slot=SLOT,
        run_id=11,
        attempt=1,
        job_result=job_result,
        mode="daily",
    )


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        ("2026-10-30T02:22:59+00:00", "2026-10-29"),
        ("2026-10-30T02:23:00+00:00", "2026-10-30"),
        ("2026-10-30T23:59:00+00:00", "2026-10-30"),
        ("2026-03-29T04:23:00+02:00", "2026-03-29"),
    ],
)
def test_daily_slot_is_utc_and_not_a_rolling_36_hour_schedule(
    instant: str, expected: str
) -> None:
    assert daily.daily_slot(datetime.fromisoformat(instant)) == expected


def _run(
    number: int,
    *,
    slot: str = SLOT,
    sha: str = SHA,
    conclusion: str = "success",
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": number,
        "run_attempt": 1,
        "display_title": f"daily-full | {slot} | {sha}",
        "event": "workflow_dispatch",
        "head_sha": sha,
        "created_at": f"{slot}T02:24:{number:02d}Z",
        "updated_at": f"{slot}T04:00:00Z",
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.com/oh-goon/oh-goon/actions/runs/{number}",
    }


def test_catch_up_reuses_first_slot_attempt_even_if_main_advanced() -> None:
    first = _run(1, status="in_progress")
    current = _run(3, sha="b" * 40)
    assert (
        daily.existing_slot_attempt([current, _run(2), first], SLOT, current_run_id=3)
        == first
    )
    assert daily.existing_slot_attempt([current], SLOT, current_run_id=3) is None
    assert daily.existing_slot_attempt([current, first], SLOT, current_run_id=1) is None
    assert daily.existing_slot_attempt([first], "2026-10-31", current_run_id=3) is None


def test_native_failure_can_have_complete_coverage_but_never_satisfies_daily() -> None:
    passed = _native()
    assert daily.native_membership(passed)[:2] == (True, True)
    failed = _native(outcome="failed")
    assert daily.native_membership(failed)[:2] == (True, False)
    skipped = _native(outcome="non_passed")
    assert daily.native_membership(skipped)[:2] == (True, False)
    result = _aggregate(
        ["native-tests"], [_owner("native-tests", report=failed, code=1)], "failure"
    )
    assert result["coverage_complete"] is True
    assert result["status"] == "FAIL"


@pytest.mark.parametrize(
    "damage",
    (
        "missing",
        "duplicates",
        "extras",
        "overlap",
        "timeout",
        "foreign-pass",
        "unreported",
    ),
)
def test_native_receipt_gaps_or_previous_pass_cannot_become_daily_success(
    damage: str,
) -> None:
    report = _native()
    assert daily.native_membership(report)[:2] == (True, True)
    if damage == "timeout":
        report["suite_timed_out"] = True
    elif damage == "foreign-pass":
        report["resume_enabled"] = True
    elif damage == "overlap":
        report["membership_receipt"]["effective_pytest_outcomes"] *= 2
    elif damage == "unreported":
        report["membership_receipt"]["effective_pytest_outcomes"][0]["pytest_outcomes"][
            0
        ]["outcome"] = "UNREPORTED"
    else:
        report["membership_receipt"][damage] = ["unaccounted-member"]
    if damage in {"foreign-pass", "overlap"}:
        with pytest.raises(ValueError):
            daily.native_membership(report)
    else:
        assert daily.native_membership(report)[1] is False


@pytest.mark.parametrize(
    "damage", ("missing", "duplicate", "extra", "sha", "attempt", "contradiction")
)
def test_aggregate_requires_one_exact_result_per_admitted_owner(damage: str) -> None:
    rows = [_owner("first"), _owner("second")]
    assert _aggregate(["first", "second"], rows)["status"] == "PASS"
    changed = deepcopy(rows)
    if damage == "missing":
        result = _aggregate(["first", "second"], changed[:1])
        assert result["status"] == "INCOMPLETE" and result["missing_owners"] == [
            "second"
        ]
        return
    if damage == "duplicate":
        changed.append(changed[0])
    elif damage == "extra":
        changed.append(_owner("third"))
    elif damage == "contradiction":
        changed[0]["exit_code"] = 7
    else:
        changed[0][damage] = "b" * 40 if damage == "sha" else 2
    with pytest.raises(ValueError, match="identity, partition or schema"):
        _aggregate(["first", "second"], changed)


@pytest.mark.parametrize("job_result", ("failure", "cancelled", "skipped"))
def test_matrix_failure_cancellation_or_skip_is_nonqualifying(job_result: str) -> None:
    rows = [_owner("first")]
    assert _aggregate(["first"], rows)["status"] == "PASS"
    assert _aggregate(["first"], rows, job_result)["status"] != "PASS"


def test_latest_red_is_fresh_and_old_green_stays_separate() -> None:
    previous = _run(1, slot="2026-10-29")
    latest = _run(2, conclusion="failure")
    green = {
        **_aggregate(["first"], [_owner("first")]),
        "run_id": 1,
        "slot": "2026-10-29",
        "completed_at": "2026-10-29T04:00:00Z",
    }
    red = {
        **_aggregate(["first"], [_owner("first", code=1)], "failure"),
        "run_id": 2,
        "coverage_complete": True,
        "status": "FAIL",
        "completed_at": "2026-10-30T04:00:00Z",
    }
    result = daily.health_summary(
        [latest, previous], [red, green], now=datetime(2026, 10, 30, 5, tzinfo=UTC)
    )
    assert result["stale"] is False and result["missed_slot"] is False
    assert result["health"] == "FAIL"
    assert result["latest_complete"]["run_id"] == 2
    assert result["last_green"]["run_id"] == 1


def test_new_incomplete_attempt_cannot_hide_behind_a_recent_green() -> None:
    previous = _run(1, slot="2026-10-29")
    latest = _run(2, conclusion="cancelled")
    green = {
        **_aggregate(["first"], [_owner("first")]),
        "run_id": 1,
        "slot": "2026-10-29",
        "completed_at": "2026-10-29T04:00:00Z",
    }
    result = daily.health_summary(
        [latest, previous], [green], now=datetime(2026, 10, 30, 5, tzinfo=UTC)
    )
    assert result["latest_attempt_state"] == "INCOMPLETE"
    assert result["latest_complete"]["run_id"] == 1
    assert result["last_green"]["run_id"] == 1
    missed = daily.health_summary(
        [previous], [green], now=datetime(2026, 10, 31, 5, tzinfo=UTC)
    )
    assert missed["missed_slot"] is True and missed["stale"] is True


def test_completion_freshness_uses_actions_time_and_rerun_attempt_time() -> None:
    previous = _run(1, slot="2026-10-28")
    green = {
        **_aggregate(["first"], [_owner("first")]),
        "run_id": 1,
        "slot": "2026-10-28",
        "completed_at": "2030-01-01T00:00:00Z",
    }
    current = _run(2, status="in_progress")
    rerun = {
        **previous,
        "run_attempt": 2,
        "status": "in_progress",
        "run_started_at": "2026-10-30T05:00:00Z",
    }
    result = daily.health_summary(
        [current, previous], [green], now=datetime(2026, 10, 30, 6, tzinfo=UTC)
    )
    assert result["stale"] is True
    assert result["latest_complete"]["completed_at"] == previous["updated_at"]
    assert (
        daily.health_summary(
            [current, rerun], [], now=datetime(2026, 10, 30, 6, tzinfo=UTC)
        )["latest_attempt"]["id"]
        == 1
    )
    incomplete = {**_aggregate(["first"], []), "run_id": 2}
    result = daily.health_summary(
        [current | {"status": "completed"}, previous],
        [green, incomplete],
        now=datetime(2026, 10, 30, 6, tzinfo=UTC),
    )
    assert result["latest_attempt_state"] == "INCOMPLETE"


@pytest.mark.process
@pytest.mark.parametrize("qualified", (False, True))
def test_nested_native_make_cannot_inherit_parent_report_destinations(
    tmp_path: Path, qualified: bool
) -> None:
    if shutil.which("make") is None:
        pytest.skip("native Make is unavailable")
    nested = tmp_path / "Makefile"
    nested.write_text(
        'check:\n\t@test -z "$$OH_GOON_PYTEST_REPORT_PATH"\n'
        '\t@test -z "$$OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY"\n'
        '\t@test -z "$$OH_GOON_VITEST_JSON_REPORT"\n'
        '\t@test "$$PYTEST_REPORT_PATH" = caller-owned-value\n',
        encoding="utf-8",
    )
    case = tmp_path / "test_nested_make.py"
    case.write_text(
        "import subprocess\n"
        "def test_nested():\n"
        f"    subprocess.run(['make', '--no-print-directory', '-s', '-f', {str(nested)!r}], check=True)\n",
        encoding="utf-8",
    )
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    report = tmp_path / "parent.json"
    command = [
        sys.executable,
        "-m",
        "oh_goon.repository_tooling.test_batches",
        str(case),
        "--pytest-config",
        str(config),
        "--granularity",
        "tests",
        "--workers",
        "1",
        "--batch-size",
        "1",
        "--batch-timeout",
        "20",
        "--report-path",
        str(report),
    ]
    if qualified:
        command.append("--qualified")
    for controls in (
        {},
        {
            "OH_GOON_PYTEST_REPORT_PATH": str(report),
            "OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY": str(tmp_path / "project-proof"),
            "OH_GOON_VITEST_JSON_REPORT": str(tmp_path / "vitest.json"),
        },
    ):
        result = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "PYTEST_REPORT_PATH": "caller-owned-value", **controls},
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            json.loads(report.read_text())["membership_receipt"]["terminal_success"]
            is True
        )


def _plan() -> list[str]:
    result = subprocess.run(
        ["make", "--no-print-directory", "-s", "release-check-plan"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return cast(list[str], json.loads(result.stdout))


@pytest.mark.process
def test_native_make_continues_independent_full_owners_after_failure(
    tmp_path: Path,
) -> None:
    make = shutil.which("make")
    if make is None:
        pytest.skip("native Make is unavailable")
    targets = _plan()
    fixture = tmp_path / "Makefile"
    events = tmp_path / "events"

    def run(fail_first: bool) -> subprocess.CompletedProcess[str]:
        fixture.write_text(
            f"include {ROOT / 'Makefile'}\n"
            + "backend-lock-check frontend-lock-check check-frontend theme-contrast:\n\t@:\n"
            + "".join(
                f"{target}:\n\t@printf '%s\\n' '{target}' >> '{events}'\n"
                + ("\t@exit 7\n" if fail_first and index == 0 else "")
                for index, target in enumerate(targets)
            ),
            encoding="utf-8",
        )
        return subprocess.run(
            [
                make,
                "--no-print-directory",
                "-f",
                str(fixture),
                "release-check",
                f"MAKE={make} --no-print-directory -f {fixture}",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
        )

    baseline = run(False)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert events.read_text().splitlines() == targets
    events.unlink()
    assert run(True).returncode != 0
    assert events.read_text().splitlines() == targets


@pytest.mark.process
def test_native_report_projects_fresh_discovery_after_rename_and_addition(
    tmp_path: Path,
) -> None:
    case = tmp_path / "test_original.py"
    case.write_text("def test_first(): assert True\n", encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nmarkers = slow: exhaustive\n", encoding="utf-8")

    def run(number: int) -> dict[str, Any]:
        report = tmp_path / f"report-{number}.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "oh_goon.repository_tooling.test_batches",
                str(tmp_path),
                "--pytest-config",
                str(config),
                "--granularity",
                "tests",
                "--workers",
                "1",
                "--batch-size",
                "4",
                "--batch-timeout",
                "20",
                "--marker",
                "not slow",
                "--report-path",
                str(report),
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return cast(dict[str, Any], json.loads(report.read_text()))

    first = run(1)
    assert first["native_collection"]["root"] == "."
    assert first["native_collection"]["discovered"] == [f"{case}::test_first"]
    case.rename(tmp_path / "test_renamed.py")
    added = tmp_path / "test_added.py"
    added.write_text(
        "import pytest\n@pytest.mark.slow\ndef test_daily(): assert True\n",
        encoding="utf-8",
    )
    second = run(2)
    assert set(second["native_collection"]["discovered"]) == {
        f"{tmp_path / 'test_renamed.py'}::test_first",
        f"{added}::test_daily",
    }
    assert second["native_collection"]["deselected"] == [f"{added}::test_daily"]
    assert second["membership_receipt"]["resumed"] == []


@pytest.mark.process
def test_original_pytest_observer_retains_each_native_outcome_category(
    tmp_path: Path,
) -> None:
    case = tmp_path / "test_native_outcomes.py"
    case.write_text(
        "import pytest\n"
        "def test_pass(): assert True\n"
        "def test_fail(): assert False\n"
        "@pytest.mark.skip(reason='native unavailable')\n"
        "def test_skip(): assert False\n"
        "@pytest.mark.xfail(reason='expected', strict=False)\n"
        "def test_xfail(): assert False\n"
        "@pytest.mark.xfail(reason='stale', strict=False)\n"
        "def test_xpass(): assert True\n",
        encoding="utf-8",
    )
    report = tmp_path / "native.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(case),
            "--granularity",
            "tests",
            "--workers",
            "1",
            "--batch-size",
            "8",
            "--batch-timeout",
            "20",
            "--pytest-arg=--maxfail=0",
            "--marker",
            "",
            "--report-path",
            str(report),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    native = json.loads(report.read_text())
    records = native["membership_receipt"]["attempt_records"]
    assert {
        item["outcome"] for row in records for item in row.get("pytest_outcomes", [])
    } == {
        "PASSED",
        "FAILED",
        "SKIPPED",
        "XFAILED",
        "XPASSED",
    }
    complete, satisfied, counts = daily.native_membership(native)
    assert complete and not satisfied
    assert counts["passed"] == 1 and counts["failed"] == 1 and counts["non_passed"] == 3


def test_daily_workflow_has_one_unfiltered_frozen_campaign_and_independent_owners() -> (
    None
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/release-validation.yml").read_text()
    )
    assert workflow["on"]["schedule"] == [{"cron": "23 2 * * *"}]
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    job = workflow["jobs"]["full-regression"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["max-parallel"] == 1
    assert (
        job["strategy"]["matrix"]["target"]
        == "${{ fromJSON(needs.campaign.outputs.targets) }}"
    )
    assert job["env"]["CHECK_PYTEST_MARKER"] == "not certification"
    assert job["env"]["PYTEST_RESUME"] == ""
    assert "maxfail" not in job["env"]["PYTEST_ADDOPTS"]
    checkout = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ needs.campaign.outputs.sha }}"
    result = workflow["jobs"]["result"]
    assert result["if"].startswith("always()")
    assert "needs.full-regression.result" in json.dumps(result)
    assert _plan() and len(set(_plan())) == len(_plan())


def test_effective_native_projection_keeps_interrupted_history_without_overriding_unique_pass(
    tmp_path: Path,
) -> None:
    report = _native()
    member = report["membership_receipt"]["selected"][0]
    store = ExecutionMembershipStore(
        tmp_path / "native",
        suite_identity="suite",
        execution_plan_identity="plan",
        run_identity="run",
    )
    store.record_terminal(
        member,
        attempt_identity="old-interrupted",
        observed_batch_identity="coarse",
        status="FAILED",
        pytest_outcomes=[{"nodeid": member, "outcome": "UNREPORTED"}],
    )
    store.record_terminal(
        member,
        attempt_identity="accepted",
        observed_batch_identity="refined",
        status="PASSED",
        pytest_outcomes=[{"nodeid": member, "outcome": "PASSED"}],
    )
    receipt, _ = store.finalize(
        selected=(member,), resumed=(), suite_passed=True, recovery_lineage=()
    )
    assert receipt.terminal_success is True
    assert receipt.failed == receipt.passed == (member,)
    assert len(receipt.attempt_records) == 2
    assert receipt.effective_pytest_outcomes[0]["attempt_identity"] == "accepted"
    report["membership_receipt"] = json.loads(json.dumps(receipt.as_dict()))
    assert daily.native_membership(report)[:2] == (True, True)
    report["membership_receipt"]["effective_pytest_outcomes"][0]["pytest_outcomes"][0][
        "outcome"
    ] = "XPASSED"
    assert daily.native_membership(report)[1] is False


@pytest.mark.process
def test_native_exit_zero_xpass_is_not_daily_pass(tmp_path: Path) -> None:
    case = tmp_path / "test_xpass.py"
    case.write_text(
        "import pytest\n@pytest.mark.xfail(reason='stale', strict=False)\ndef test_unexpected(): assert True\n",
        encoding="utf-8",
    )
    report = tmp_path / "native.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(case),
            "--granularity",
            "tests",
            "--batch-size",
            "1",
            "--workers",
            "1",
            "--batch-timeout",
            "20",
            "--marker",
            "",
            "--report-path",
            str(report),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    native = json.loads(report.read_text())
    assert (
        native["status"] == "PASS"
        and native["membership_receipt"]["terminal_success"] is True
    )
    assert daily.native_membership(native)[:2] == (True, False)


def test_daily_slot_cannot_reinterpret_a_naive_clock_in_the_host_timezone() -> None:
    with pytest.raises(ValueError, match="explicit timezone"):
        daily.daily_slot(datetime(2026, 10, 30, 2, 23))


@pytest.mark.process
def test_full_native_discovery_retains_daily_and_slow_as_distinct_cadences(
    tmp_path: Path,
) -> None:
    case = tmp_path / "test_cadence.py"
    case.write_text(
        "import pytest\ndef test_fast(): pass\n@pytest.mark.daily\ndef test_journey(): pass\n@pytest.mark.slow\ndef test_exhaustive(): pass\n@pytest.mark.certification\ndef test_privileged(): pass\n",
        encoding="utf-8",
    )
    invocation = resolve_pytest_invocation(
        pytest_config=ROOT / "backend/pyproject.toml"
    )
    full = collect_native_pytest(
        (case,), marker="not certification", timeout=20, invocation=invocation
    )
    selected = collect_native_pytest(
        (case,),
        marker="not certification and not daily and not slow",
        timeout=20,
        invocation=invocation,
    )
    assert {row["selector"].split("::")[-1] for row in full.selected} == {
        "test_fast",
        "test_journey",
        "test_exhaustive",
    }
    assert {row["selector"].split("::")[-1] for row in selected.selected} == {
        "test_fast"
    }


@pytest.mark.parametrize(
    "damage",
    ("missing", "added", "duplicate", "unexplained-exclusion", "cadence", "root"),
)
def test_native_collection_must_match_the_original_execution_membership(
    damage: str,
) -> None:
    report = _native()
    assert daily.native_collection_instances(report)[0] == {
        "./test_case.py::test_behavior"
    }
    if damage == "missing":
        report["native_collection"]["discovered"] = []
    elif damage == "added":
        report["native_collection"]["discovered"].append("test_other.py::test_added")
    elif damage == "duplicate":
        report["native_collection"]["discovered"] *= 2
    elif damage == "unexplained-exclusion":
        report["native_collection"]["deselected"] = ["test_other.py::test_added"]
    elif damage == "cadence":
        report["marker"] = "not certification and not daily"
    else:
        report["native_collection"]["root"] = "../foreign"
    with pytest.raises(ValueError):
        daily.native_collection_instances(report)


@pytest.mark.process
@pytest.mark.parametrize(
    ("revision", "slot", "allowed"),
    [(SHA, SLOT, True), ("main", SLOT, False), (SHA, "2026-02-30", False)],
)
def test_campaign_rejects_invalid_exact_inputs_before_checkout(
    tmp_path: Path, revision: str, slot: str, allowed: bool
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("native Bash unavailable")
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/release-validation.yml").read_text()
    )
    steps = workflow["jobs"]["campaign"]["steps"]
    script = steps[0]["run"]
    assert steps[1]["uses"].startswith("actions/checkout@")
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env={"PATH": os.defpath, "CAMPAIGN_REF": revision, "CAMPAIGN_SLOT": slot},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) is allowed, result.stdout + result.stderr


def test_changed_source_never_qualifies_or_rewrites_native_owner_exit() -> None:
    valid = _owner("native-tests", report=_native())
    assert valid["satisfied"] is True
    changed = daily.owner_result(
        target="native-tests",
        sha=SHA,
        slot=SLOT,
        run_id=11,
        attempt=1,
        exit_code=0,
        native_report=_native(),
        requires_native_report=True,
        source_status="CHANGED",
    )
    assert changed["exit_code"] == 0
    assert changed["coverage_complete"] is False and changed["satisfied"] is False


def _project_evidence(tmp_path: Path) -> Path:
    projects = []
    for name in ("iris", "orders"):
        file = name + ".junit.xml"
        (tmp_path / file).write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="app" name="test_native"/></testsuite></testsuites>',
            encoding="utf-8",
        )
        projects.append(
            {
                "name": name,
                "junit_file": file,
                "sync_exit_code": 0,
                "pytest_exit_code": 0,
                "phase": "COMPLETE",
                "process_cleanup": "VERIFIED",
            }
        )
    path = tmp_path / "summary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "oh-goon.python-project-checks/v1",
                "projects": projects,
                "cleanup": "VERIFIED",
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "damage",
    (
        "missing",
        "zero",
        "unstarted",
        "cleanup",
        "skipped",
        "counter",
        "contradiction",
        "traversal",
    ),
)
def test_native_project_junit_and_full_owner_context_are_both_required(
    tmp_path: Path, damage: str
) -> None:
    path = _project_evidence(tmp_path)
    assert daily.project_test_evidence(path)[:2] == (True, True)
    summary = json.loads(path.read_text())
    xml = tmp_path / "iris.junit.xml"
    if damage == "missing":
        xml.unlink()
    elif damage == "zero":
        xml.write_text(
            '<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0"/></testsuites>'
        )
    elif damage == "unstarted":
        summary["projects"][0]["phase"] = "UNSTARTED"
    elif damage == "cleanup":
        summary["cleanup"] = "INCOMPLETE"
    elif damage in {"skipped", "counter"}:
        xml.write_text(
            f'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="{1 if damage == "skipped" else 0}"><testcase classname="app" name="test_native"><skipped type="pytest.xfail"/></testcase></testsuite></testsuites>'
        )
    elif damage == "traversal":
        summary["projects"][0]["junit_file"] = "../foreign.xml"
    else:
        summary["projects"][0]["phase"] = "PYTEST_FAILED"
    path.write_text(json.dumps(summary))
    if damage in {"counter", "contradiction", "traversal"}:
        with pytest.raises(ValueError):
            daily.project_test_evidence(path)
    else:
        assert daily.project_test_evidence(path)[1] is False


@pytest.mark.process
@pytest.mark.parametrize("case", ("pass", "failure", "unknown", "source-change"))
def test_github_shell_records_failure_and_refuses_unknown_or_changed_source(
    tmp_path: Path, case: str
) -> None:
    git, bash = shutil.which("git"), shutil.which("bash")
    if git is None or bash is None:
        pytest.skip("native Git and Bash required")
    workspace, tools, runner = (
        tmp_path / "workspace",
        tmp_path / "tools",
        tmp_path / "runner",
    )
    for directory in (workspace, tools, runner):
        directory.mkdir()
    environment = {
        "PATH": str(tools) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run([git, "init", "-q", str(workspace)], env=environment, check=True)
    subprocess.run(
        [
            git,
            "-C",
            str(workspace),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        env=environment,
        check=True,
    )
    sha = subprocess.check_output(
        [git, "-C", str(workspace), "rev-parse", "HEAD"], env=environment, text=True
    ).strip()
    events = tmp_path / "events"
    for name in ("make", "uv"):
        tool = tools / name
        tool.write_text(
            '#!/bin/sh\nprintf "%s:%s\\n" "${0##*/}" "$*" >> "$EVENTS"\nif [ "${0##*/}" = make ]; then\n if [ "$SOURCE_CHANGE" = 1 ]; then touch changed.txt; fi\n exit "$MAKE_STATUS"\nfi\n',
            encoding="utf-8",
        )
        tool.chmod(0o755)
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/release-validation.yml").read_text()
    )
    script = next(
        step["run"]
        for step in workflow["jobs"]["full-regression"]["steps"]
        if step.get("id") == "owner"
    )
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=workspace,
        env={
            **environment,
            "FROZEN_SHA": sha,
            "FULL_TARGET": "unknown" if case == "unknown" else "architecture",
            "RUNNER_TEMP": str(runner),
            "FULL_SLOT": SLOT,
            "GITHUB_RUN_ID": "11",
            "GITHUB_RUN_ATTEMPT": "1",
            "MAKE_STATUS": "7" if case == "failure" else "0",
            "SOURCE_CHANGE": "1" if case == "source-change" else "0",
            "EVENTS": str(events),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (
        result.returncode
        == {"pass": 0, "failure": 7, "unknown": 2, "source-change": 2}[case]
    ), result.stdout + result.stderr
    if case == "unknown":
        assert not events.exists()
    else:
        trace = events.read_text()
        assert "uv:run" in trace
        assert f"--exit-code {'7' if case == 'failure' else '0'}" in trace
        assert (
            f"--source-status {'CHANGED' if case == 'source-change' else 'VERIFIED'}"
            in trace
        )
