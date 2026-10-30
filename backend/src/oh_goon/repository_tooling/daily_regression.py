"""Join existing Actions campaign metadata and native owner results; never execute tests."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.frontend_test_results import vitest_outcomes
from oh_goon.repository_tooling.pytest_execution_membership import (
    MEMBERSHIP_SCHEMA,
    PYTEST_NATIVE_OUTCOMES,
)
from oh_goon.repository_tooling.pytest_execution_report import execution_report_schema

SCHEMA = "oh-goon.daily-regression/v1"
MAX_JSON_BYTES = 64 * 1024 * 1024
_TITLE = re.compile(r"^daily-full \| (scheduled|\d{4}-\d{2}-\d{2}) \| ([0-9a-f]{40})$")


def daily_slot(now: datetime) -> str:
    """Most recent 02:23 UTC slot, including before today's scheduled time."""
    if now.tzinfo is None:
        raise ValueError("daily slot requires an explicit timezone")
    current = now.astimezone(UTC)
    day = current.date()
    if current.timetz().replace(tzinfo=None) < time(2, 23):
        day -= timedelta(days=1)
    return day.isoformat()


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Actions timestamps require a timezone")
    return parsed.astimezone(UTC)


def _sha(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("one immutable Git commit SHA is required")
    return value


def _slot(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("daily slot must be YYYY-MM-DD")
    return value


def _repository(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None:
        raise ValueError("GitHub repository must be owner/name")
    return value


def _read_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON_BYTES:
            raise ValueError("campaign evidence is not a bounded regular JSON file")
        payload = source.read(MAX_JSON_BYTES + 1)
        after = os.fstat(source.fileno())
    if len(payload) > MAX_JSON_BYTES or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("campaign evidence changed during observation")
    return payload


def _read(path: Path) -> dict[str, Any]:
    parsed = json.loads(_read_bytes(path))
    if not isinstance(parsed, dict):
        raise ValueError("campaign evidence must be an object")
    return parsed


def project_test_evidence(path: Path) -> tuple[bool, bool, dict[str, Any]]:
    """Read native pytest JUnit produced by the original locked project owner."""
    summary = _read(path)
    rows = summary.get("projects")
    if (
        summary.get("schema") != "oh-goon.python-project-checks/v1"
        or not isinstance(rows, list)
        or not rows
    ):
        raise ValueError("Python project evidence schema or membership is missing")
    complete = summary.get("cleanup") == "VERIFIED"
    satisfied = complete
    outcomes: dict[str, Any] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or row["name"] in outcomes
        ):
            raise ValueError("Python project evidence contains invalid or duplicate projects")
        file = row.get("junit_file")
        if not isinstance(file, str) or Path(file).name != file:
            raise ValueError("Python project JUnit reference is invalid")
        if (
            row.get("phase") not in {"COMPLETE", "PYTEST_FAILED"}
            or row.get("sync_exit_code") != 0
            or row.get("pytest_exit_code") not in {0, 1}
            or row.get("process_cleanup") != "VERIFIED"
            or not (path.parent / file).is_file()
        ):
            complete = satisfied = False
            outcomes[row["name"]] = {"state": "INCOMPLETE", "phase": row.get("phase")}
            continue
        payload = _read_bytes(path.parent / file)
        if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
            raise ValueError("native JUnit cannot declare entities")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise ValueError("native project JUnit is incomplete or invalid") from error
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        cases = list(root.iter("testcase"))
        counts = {
            name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
            for name in ("tests", "failures", "errors", "skipped")
        }
        if any(value < 0 for value in counts.values()):
            raise ValueError("native JUnit counts cannot be negative")
        observed = {
            name: sum(case.find(tag) is not None for case in cases)
            for name, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped"))
        }
        if any(counts[name] != observed[name] for name in observed):
            raise ValueError("native JUnit outcome records contradict its counts")
        if (row["phase"] == "COMPLETE") != (row["pytest_exit_code"] == 0):
            raise ValueError("native project phase contradicts its exit code")
        if not suites or not cases or counts["tests"] != len(cases):
            complete = satisfied = False
        bad = counts["failures"] + counts["errors"] + counts["skipped"]
        satisfied = satisfied and row["pytest_exit_code"] == 0 and bad == 0
        outcomes[row["name"]] = {"native_junit_outcomes": counts, "phase": row["phase"]}
    return complete, satisfied, outcomes


def frontend_test_evidence(path: Path) -> tuple[bool, bool, dict[str, int]]:
    """Consume Vitest's native configured-test result, retaining non-PASS outcomes."""
    return vitest_outcomes(_read(path))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(payload, target, sort_keys=True)
        target.write("\n")


def campaign_runs(repository: str) -> list[dict[str, Any]]:
    """Use native gh for one bounded recent metadata page, without a new API client."""
    repository = _repository(repository)
    result = run_command(
        "gh",
        "api",
        "--method",
        "GET",
        f"repos/{repository}/actions/workflows/release-validation.yml/runs",
        "-f",
        "per_page=100",
        "--jq",
        "{runs: [.workflow_runs[] | {id,run_attempt,display_title,head_sha,status,conclusion,created_at,run_started_at,updated_at,html_url,event}]}",
        timeout=30,
        max_capture_bytes_per_stream=2 * 1024 * 1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode:
        raise RuntimeError("Actions campaign metadata is unavailable")
    parsed = json.loads(result.stdout)
    rows = parsed.get("runs") if isinstance(parsed, dict) else None
    if (
        not isinstance(rows, list)
        or len(rows) > 100
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise ValueError("Actions campaign metadata is invalid")
    return rows


def _daily_identity(run: dict[str, Any]) -> tuple[str, str] | None:
    title = run.get("display_title", "")
    match = _TITLE.fullmatch(title) if isinstance(title, str) else None
    if match is None or run.get("event") not in {"schedule", "workflow_dispatch"}:
        return None
    selected_slot, sha = match.groups()
    if selected_slot == "scheduled":
        selected_slot = daily_slot(_instant(str(run["created_at"])))
    return _slot(selected_slot), _sha(sha)


def existing_slot_attempt(
    rows: list[dict[str, Any]], slot: str, *, current_run_id: int
) -> dict[str, Any] | None:
    """One daily slot keeps its first source and original Actions rerun lineage."""
    matches = [
        row
        for row in rows
        if (identity := _daily_identity(row)) is not None and identity[0] == slot
    ]
    original = min(matches, key=lambda row: _instant(str(row["created_at"]))) if matches else None
    return original if original is not None and int(original["id"]) != current_run_id else None


def native_membership(report: dict[str, Any]) -> tuple[bool, bool, dict[str, int]]:
    """Consume original batch receipts; no log parsing or alternate outcome calculation."""
    if report.get("schema") != execution_report_schema():
        raise ValueError("unknown native batch execution report schema")
    receipt = report.get("membership_receipt")
    if not isinstance(receipt, dict) or receipt.get("schema") != MEMBERSHIP_SCHEMA:
        raise ValueError("native membership receipt is missing")
    if report.get("resume_enabled") is not False or report.get("reused_member_count") != 0:
        raise ValueError("daily execution cannot reuse a previous test PASS")
    records = receipt.get("effective_pytest_outcomes")
    if not isinstance(records, list):
        raise ValueError("original effective native outcome projection is missing")
    observed: set[str] = set()
    unreported = False
    native_pass = True
    effective_groups: dict[str, set[str]] = {
        name: set() for name in ("passed", "failed", "non_passed")
    }
    members: set[str] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("member"), str)
            or record["member"] in members
        ):
            raise ValueError("effective native membership repeats or omits its identity")
        member = record["member"]
        members.add(member)
        native_status = record.get("status")
        status = (
            {"PASSED": "passed", "FAILED": "failed", "NON_PASSED": "non_passed"}.get(native_status)
            if isinstance(native_status, str)
            else None
        )
        if status is None:
            unreported = True
            continue
        effective_groups[status].add(member)
        outcomes = record.get("pytest_outcomes")
        if not isinstance(outcomes, list) or not outcomes:
            unreported = True
            continue
        if any(
            not isinstance(row, dict)
            or set(row) != {"nodeid", "outcome"}
            or not isinstance(row.get("nodeid"), str)
            or not row["nodeid"]
            or row.get("outcome") not in PYTEST_NATIVE_OUTCOMES
            for row in outcomes
        ) or len({row["nodeid"] for row in outcomes}) != len(outcomes):
            raise ValueError("native pytest outcome categories are invalid")
        observed.add(member)
        unreported = unreported or any(row["outcome"] == "UNREPORTED" for row in outcomes)
        native_pass = native_pass and all(row["outcome"] == "PASSED" for row in outcomes)
    names = (
        "selected",
        "attempted",
        "completed",
        "passed",
        "failed",
        "non_passed",
        "missing",
        "duplicates",
        "extras",
        "resumed",
    )
    groups: dict[str, set[str]] = {}
    for name in names:
        value = receipt.get(name)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("native membership group is invalid")
        if len(set(value)) != len(value):
            raise ValueError("native membership contains repeated members")
        groups[name] = set(value)
    groups.update(effective_groups)
    passed, failed, other = (groups[name] for name in ("passed", "failed", "non_passed"))
    partition = not (passed & failed or passed & other or failed & other)
    complete = (
        partition
        and not unreported
        and bool(groups["selected"])
        and members == groups["selected"]
        and groups["selected"] == groups["completed"] == (passed | failed | other)
        and groups["attempted"] == groups["selected"]
        and not any(groups[name] for name in ("missing", "duplicates", "extras", "resumed"))
        and groups["completed"] <= observed
        and report.get("suite_timed_out") is False
    )
    satisfied = (
        complete
        and not failed
        and not other
        and native_pass
        and report.get("status") == "PASS"
        and receipt.get("terminal_success") is True
    )
    return complete, satisfied, {name: len(group) for name, group in groups.items()}


def native_collection_instances(report: dict[str, Any]) -> tuple[set[str], str, set[str]]:
    """Reconcile the original discovered scope with its exact native membership."""
    native_membership(report)
    collection = report.get("native_collection")
    if not isinstance(collection, dict) or report.get("marker") != "not certification":
        raise ValueError("complete portable native discovery evidence is unavailable")
    root = collection.get("root")
    discovered, deselected = collection.get("discovered"), collection.get("deselected")
    if (
        not isinstance(root, str)
        or Path(root).is_absolute()
        or ".." in Path(root).parts
        or not isinstance(discovered, list)
        or not isinstance(deselected, list)
        or any(not isinstance(item, str) for item in (*discovered, *deselected))
    ):
        raise ValueError("original native discovery scope is invalid")
    if (
        len(set(discovered)) != len(discovered)
        or len(set(deselected)) != len(deselected)
        or not set(deselected) <= set(discovered)
    ):
        raise ValueError("original native discovery contains duplicates or unexplained exclusions")
    selected = set(discovered) - set(deselected)
    admitted = set(report["membership_receipt"]["selected"])
    expected = (
        selected
        if report.get("granularity") == "tests"
        else {member.partition("::")[0] for member in selected}
        if report.get("granularity") == "files"
        else None
    )
    if not selected or expected is None or admitted != expected:
        raise ValueError("native discovery and executed admission membership differ")
    identity = report.get("source_identity")
    if not isinstance(identity, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None:
        raise ValueError("native source identity is unavailable")
    return (
        {f"{root}/{member}" for member in selected},
        identity,
        {f"{root}/{member}" for member in deselected},
    )


def owner_result(
    *,
    target: str,
    sha: str,
    slot: str,
    run_id: int,
    attempt: int,
    exit_code: int,
    native_report: dict[str, Any] | None,
    requires_native_report: bool,
    behavioral_evidence: tuple[bool, bool, dict[str, Any]] | None = None,
    source_status: str = "VERIFIED",
) -> dict[str, Any]:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,79}", target) is None:
        raise ValueError("invalid canonical owner name")
    complete, satisfied = exit_code == 0, exit_code == 0
    membership: dict[str, int] | None = None
    if native_report is not None:
        complete, satisfied, membership = native_membership(native_report)
        satisfied = satisfied and exit_code == 0
    elif requires_native_report:
        complete = satisfied = False
    if behavioral_evidence is not None:
        complete, satisfied, _details = behavioral_evidence
        satisfied = satisfied and exit_code == 0
    if source_status != "VERIFIED":
        complete = satisfied = False
    return {
        "schema": SCHEMA,
        "kind": "owner",
        "target": target,
        "sha": _sha(sha),
        "slot": _slot(slot),
        "run_id": run_id,
        "attempt": attempt,
        "exit_code": exit_code,
        "source_status": source_status,
        "coverage_complete": complete,
        "satisfied": satisfied,
        "membership": membership,
        "native_attempt_outcomes": dict(
            Counter(
                item["outcome"]
                for record in native_report["membership_receipt"]["attempt_records"]
                for item in record.get("pytest_outcomes", [])
            )
        )
        if native_report is not None
        else None,
        "behavioral_evidence": behavioral_evidence[2] if behavioral_evidence is not None else None,
        "native_source_identity": native_report.get("source_identity") if native_report else None,
        "non_passed_detail": "Native pytest -ra output retains SKIP/XFAIL/XPASS reasons; these members are not PASS."
        if membership and membership["non_passed"]
        else None,
    }


def aggregate_results(
    targets: list[str],
    rows: list[dict[str, Any]],
    *,
    sha: str,
    slot: str,
    run_id: int,
    attempt: int,
    job_result: str,
    mode: str,
    hosted_example_result: str = "success",
) -> dict[str, Any]:
    if (
        not targets
        or len(set(targets)) != len(targets)
        or any(re.fullmatch(r"[a-z][a-z0-9-]{0,79}", target) is None for target in targets)
    ):
        raise ValueError("canonical full-owner plan must be nonempty and unique")
    by_target: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            row.get("schema") != SCHEMA
            or row.get("kind") != "owner"
            or row.get("sha") != sha
            or row.get("slot") != slot
            or row.get("run_id") != run_id
            or row.get("attempt") != attempt
            or row.get("target") not in targets
            or row["target"] in by_target
            or type(row.get("coverage_complete")) is not bool
            or type(row.get("satisfied")) is not bool
            or type(row.get("exit_code")) is not int
            or (
                row.get("satisfied") is True
                and (row.get("exit_code") != 0 or row.get("coverage_complete") is not True)
            )
        ):
            raise ValueError("full-owner result identity, partition or schema differs")
        by_target[row["target"]] = row
    missing = sorted(set(targets) - by_target.keys())
    complete = not missing and all(row["coverage_complete"] for row in rows)
    complete = complete and hosted_example_result == "success"
    success = (
        complete
        and job_result == "success"
        and hosted_example_result == "success"
        and all(row["satisfied"] for row in rows)
    )
    return {
        "schema": SCHEMA,
        "kind": "campaign",
        "mode": mode,
        "sha": _sha(sha),
        "slot": _slot(slot),
        "run_id": run_id,
        "attempt": attempt,
        "coverage_complete": complete,
        "status": "PASS" if success else "FAIL" if complete else "INCOMPLETE",
        "missing_owners": missing,
        "hosted_python_example": hosted_example_result,
        "owners": rows,
        "completed_at": datetime.now(UTC).isoformat(),
        "environment": {
            "os": "ubuntu-26.04",
            "python": "3.12",
            "node": "24.19.0",
            "uv": "0.12.5",
            "cpu_quota": 2,
            "max_parallel_owners": 1,
        },
        "excluded_capabilities": [
            "certification marker: separately selected privileged/native/model/APP qualification",
            "Orders airflow marker: optional comparison requires its separately selected dependency group",
            "other OS/Python/browser variants: not executed by this portable matrix",
        ],
    }


def health_summary(
    rows: list[dict[str, Any]], results: list[dict[str, Any]], *, now: datetime
) -> dict[str, Any]:
    daily = [row for row in rows if _daily_identity(row) is not None]
    latest = (
        max(daily, key=lambda row: _instant(str(row.get("run_started_at") or row["created_at"])))
        if daily
        else None
    )
    admitted: list[dict[str, Any]] = []
    by_id = {int(row["id"]): row for row in daily}
    for result in results:
        identity = result.get("run_id")
        run = by_id.get(identity) if type(identity) is int else None
        if run is None or run.get("status") != "completed" or result.get("mode") != "daily":
            continue
        if (
            result.get("schema") == SCHEMA
            and result.get("kind") == "campaign"
            and result.get("attempt") == run.get("run_attempt")
            and (result.get("slot"), result.get("sha")) == _daily_identity(run)
            and result.get("coverage_complete") is True
        ):
            admitted.append(
                {
                    **result,
                    "completed_at": run["updated_at"],
                    "status": result["status"] if run.get("conclusion") == "success" else "FAIL",
                }
            )
    admitted.sort(key=lambda result: _instant(result["completed_at"]), reverse=True)
    complete = admitted[0] if admitted else None
    green = next((result for result in admitted if result.get("status") == "PASS"), None)
    expected_slot = daily_slot(now)
    slot_attempts = [row for row in daily if _daily_identity(row)[0] == expected_slot]  # type: ignore[index]
    stale = complete is None or now.astimezone(UTC) - _instant(
        complete["completed_at"]
    ) > timedelta(hours=36)
    overdue = [
        row["id"]
        for row in daily
        if row.get("status") != "completed"
        and (identity := _daily_identity(row)) is not None
        and identity[0] < expected_slot
    ]
    latest_result = next(
        (
            result
            for result in results
            if latest is not None
            and result.get("run_id") == latest["id"]
            and result.get("attempt") == latest["run_attempt"]
            and result.get("schema") == SCHEMA
            and (result.get("slot"), result.get("sha")) == _daily_identity(latest)
        ),
        None,
    )
    latest_state = (
        "COALESCED"
        if latest_result and latest_result.get("kind") == "coalesced"
        else "IN_PROGRESS"
        if latest and latest.get("status") != "completed"
        else "INCOMPLETE"
        if latest and (not latest_result or latest_result.get("coverage_complete") is not True)
        else latest_result["status"]
        if latest_result and latest is not None and latest.get("conclusion") == "success"
        else "FAIL"
        if latest_result
        else "UNKNOWN"
    )
    return {
        "expected_slot": expected_slot,
        "missed_slot": not slot_attempts,
        "latest_attempt": latest,
        "latest_complete": complete,
        "last_green": green,
        "stale": stale,
        "overdue_attempts": overdue,
        "health": complete["status"] if complete else "UNKNOWN",
        "latest_attempt_state": latest_state,
        "history_window": "100 recent Actions runs; missing/expired result artifacts remain unknown",
    }


def _download(repository: str, run_id: int, name: str, directory: Path) -> bool:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = run_command(
        "gh",
        "run",
        "download",
        str(run_id),
        "--repo",
        _repository(repository),
        "--name",
        name,
        "--dir",
        str(directory),
        timeout=60,
        max_capture_bytes_per_stream=8192,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--mode", choices=("daily", "release"), required=True)
    prepare.add_argument("--slot", default="")
    prepare.add_argument("--sha", required=True)
    prepare.add_argument("--run-id", type=int, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    record = commands.add_parser("record-owner")
    record.add_argument("--target", required=True)
    record.add_argument("--sha", required=True)
    record.add_argument("--slot", required=True)
    record.add_argument("--run-id", type=int, required=True)
    record.add_argument("--attempt", type=int, required=True)
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--source-status", choices=("VERIFIED", "CHANGED"), required=True)
    record.add_argument("--native-report", type=Path)
    record.add_argument("--requires-native-report", action="store_true")
    record.add_argument("--project-report", type=Path)
    record.add_argument("--vitest-report", type=Path)
    record.add_argument("--output", type=Path, required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--repository", required=True)
    aggregate.add_argument("--mode", choices=("daily", "release"), required=True)
    aggregate.add_argument("--sha", required=True)
    aggregate.add_argument("--slot", required=True)
    aggregate.add_argument("--run-id", type=int, required=True)
    aggregate.add_argument("--attempt", type=int, required=True)
    aggregate.add_argument("--targets", required=True)
    aggregate.add_argument("--job-result", required=True)
    aggregate.add_argument("--hosted-example-result", required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    health = commands.add_parser("health")
    health.add_argument("--repository", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            rows = campaign_runs(args.repository)
            current = next((row for row in rows if int(row["id"]) == args.run_id), None)
            if current is None:
                response = run_command(
                    "gh",
                    "api",
                    f"repos/{_repository(args.repository)}/actions/runs/{args.run_id}",
                    timeout=30,
                    max_capture_bytes_per_stream=2 * 1024 * 1024,
                    require_process_group_quiescence=True,
                    parent_death_signal=True,
                )
                current = json.loads(response.stdout) if response.returncode == 0 else None
                if not isinstance(current, dict) or current.get("id") != args.run_id:
                    raise ValueError("current immutable Actions run metadata is unavailable")
                rows.append(current)
            slot = (
                _slot(args.slot) if args.slot else daily_slot(_instant(str(current["created_at"])))
            )
            previous = (
                existing_slot_attempt(rows, slot, current_run_id=args.run_id)
                if args.mode == "daily"
                else None
            )
            result = {
                "schema": SCHEMA,
                "kind": "coalesced" if previous else "admission",
                "run": previous is None,
                "sha": _sha(args.sha),
                "slot": slot,
                "mode": args.mode,
                "run_id": args.run_id,
                "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
                "existing_attempt": previous,
            }
            _write(args.output, result)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                for key in ("run", "sha", "slot", "mode"):
                    output.write(
                        f"{key}={str(result[key]).lower() if key == 'run' else result[key]}\n"
                    )
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "record-owner":
            report = (
                _read(args.native_report)
                if args.native_report and args.native_report.exists()
                else None
            )
            behavioral_evidence = None
            if args.project_report is not None:
                behavioral_evidence = project_test_evidence(args.project_report)
            if args.vitest_report is not None:
                if behavioral_evidence is not None:
                    raise ValueError("one native behavior report owner is required")
                behavioral_evidence = frontend_test_evidence(args.vitest_report)
            _write(
                args.output,
                owner_result(
                    target=args.target,
                    sha=args.sha,
                    slot=args.slot,
                    run_id=args.run_id,
                    attempt=args.attempt,
                    exit_code=args.exit_code,
                    native_report=report,
                    requires_native_report=args.requires_native_report,
                    behavioral_evidence=behavioral_evidence,
                    source_status=args.source_status,
                ),
            )
            return 0
        if args.command == "aggregate":
            targets = json.loads(args.targets)
            if not isinstance(targets, list) or any(
                not isinstance(target, str) for target in targets
            ):
                raise ValueError("canonical full owner plan is invalid")
            rows = []
            with tempfile.TemporaryDirectory(prefix="oh-goon-daily-results-") as temporary:
                instances: set[str] = set()
                source_identities: set[str] = set()
                for target in targets:
                    directory = Path(temporary) / target
                    if _download(
                        args.repository,
                        args.run_id,
                        f"full-owner-{target}-{args.attempt}",
                        directory,
                    ):
                        row = _read(directory / "owner.json")
                        rows.append(row)
                        if row.get("membership") is not None:
                            report = _read(directory / "pytest.json")
                            qualified, identity, _excluded = native_collection_instances(report)
                            if instances & qualified:
                                raise ValueError(
                                    "daily native test instances overlap between owners"
                                )
                            instances.update(qualified)
                            source_identities.add(identity)
                            if len(source_identities) > 1:
                                raise ValueError("daily owner source identities differ")
            result = aggregate_results(
                targets,
                rows,
                sha=args.sha,
                slot=args.slot,
                run_id=args.run_id,
                attempt=args.attempt,
                job_result=args.job_result,
                mode=args.mode,
                hosted_example_result=args.hosted_example_result,
            )
            _write(args.output, result)
            summary = (
                "## Full regression\n\n"
                + f"Slot: {args.slot}; source: `{args.sha}`; attempt: {args.run_id}/{args.attempt}.\n\n"
                + f"Result: **{result['status']}**; complete portable coverage: **{result['coverage_complete']}**.\n\n"
                + "| Owner | Exit | Coverage complete | Satisfied |\n|---|---:|---|---|\n"
                + "".join(
                    f"| {row['target']} | {row['exit_code']} | {row['coverage_complete']} | {row['satisfied']} |\n"
                    for row in rows
                )
                + f"\nMissing owners: {', '.join(result['missing_owners']) or 'none'}.\n\n"
                + "Portable environment: Ubuntu 26.04, Python 3.12, Node 24.19.0, uv 0.12.5; at most one owner runner and two CPUs per test scope. Certification and other host/browser variants are not claimed.\n"
            )
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as output:
                output.write(summary)
            return 0 if result["status"] == "PASS" else 1
        rows = campaign_runs(args.repository)
        results = []
        with tempfile.TemporaryDirectory(prefix="oh-goon-daily-health-") as temporary:
            completed = [
                row
                for row in rows
                if _daily_identity(row) is not None and row.get("status") == "completed"
            ][:20]
            for row in completed:
                directory = Path(temporary) / str(row["id"])
                if _download(
                    args.repository, int(row["id"]), f"daily-result-{row['run_attempt']}", directory
                ):
                    results.append(_read(directory / "campaign.json"))
        health_report = health_summary(rows, results, now=datetime.now(UTC))
        print(json.dumps(health_report, indent=2, sort_keys=True))
        return (
            1
            if health_report["stale"]
            or health_report["missed_slot"]
            or health_report["health"] != "PASS"
            or health_report["overdue_attempts"]
            or health_report["latest_attempt_state"] in {"INCOMPLETE", "UNKNOWN"}
            else 0
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Daily regression evidence INCOMPLETE: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
