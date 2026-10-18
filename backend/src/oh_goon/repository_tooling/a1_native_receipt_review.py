"""A1d offline integrity review of native diagnostic receipts; NEVER admission or APP PASS.

This reviewer consumes independently supplied complete source bytes and one WSL
attempt. File contents, status strings and log output are not host attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

EXPECTED = (
    "01_source_transport_clean", "02_sync", "02b_schedule_plan_inputs",
    "03_native_issuer_wheel", "04_architecture", "05_verify_p0", "06_test",
    "07_frontend_functional", "08_certify",
)
SHA = re.compile(r"[0-9a-f]{64}\Z")


class ReceiptError(ValueError):
    pass


def require(ok: bool, detail: str) -> None:
    if not ok:
        raise ReceiptError(detail)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_archive(source: Path, expected: str) -> dict[str, str]:
    require(bool(SHA.fullmatch(expected)), "invalid expected SHA-256")
    require(source.is_file() and not source.is_symlink(), "candidate source unavailable")
    require(sha(source.read_bytes()) == expected, "candidate archive identity mismatch")
    members: dict[str, str] = {}
    with ZipFile(source) as archive:
        for info in archive.infolist():
            name = info.filename
            path = PurePosixPath(name)
            require(not name.startswith("/") and name == path.as_posix()
                    and not any(p in (".", "..") for p in path.parts)
                    and not name.endswith("/"), "archive contains unsafe or non-file member")
            mode = (info.external_attr >> 16) & 0xffff
            require(not mode or stat.S_ISREG(mode), "archive contains symlink/special entry")
            require(name not in members, "duplicate source member")
            members[name] = sha(archive.read(info))
    require(bool(members), "empty candidate archive")
    return members


def file_at(directory: Path, name: str) -> bytes:
    path = directory / name
    require(path.is_file() and not path.is_symlink(), f"missing or linked {name}")
    return path.read_bytes()


def key_values(data: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        require("=" in line, "malformed provenance entry")
        key, value = line.split("=", 1)
        require(bool(key) and key not in result, "duplicate/empty provenance key")
        result[key] = value
    return result


def manifest(data: bytes) -> dict[str, str]:
    require(bool(data) and data.endswith(b"\n"), "empty or truncated source manifest")
    result: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        require(len(line) >= 68 and line[64:68] == "  ./", "invalid source manifest record")
        digest, relative = line[:64], line[68:]
        require(bool(SHA.fullmatch(digest)), "invalid file SHA-256")
        path = PurePosixPath(relative)
        require(relative == path.as_posix() and not relative.startswith("/")
                and not any(p in (".", "..") for p in path.parts)
                and relative not in result, "unsafe or duplicate manifest entry")
        result[relative] = digest
    return result


def manifest_logs(data: bytes) -> dict[str, str]:
    require(bool(data) and data.endswith(b"\n"), "missing or truncated log manifest")
    result: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        require(len(line) >= 67 and line[64:66] == "  ", "malformed log manifest record")
        digest, name = line[:64], line[66:]
        require(bool(SHA.fullmatch(digest)), "invalid command log digest")
        require(name in {label + ".log" for label in EXPECTED} and name not in result,
                "unknown or duplicate command log name")
        result[name] = digest
    return result


def steps_table(data: bytes) -> list[tuple[str, int, str]]:
    lines = data.decode("utf-8").splitlines()
    require(len(lines) >= 2 and lines[0] == "step\texit_code\tclassification",
            "missing step receipts")
    records: list[tuple[str, int, str]] = []
    for index, line in enumerate(lines[1:]):
        parts = line.split("\t")
        require(len(parts) == 3 and re.fullmatch(r"[0-9]+", parts[1]) is not None,
                "malformed step receipt")
        label, rc_str, kind = parts
        rc = int(rc_str)
        if index < len(EXPECTED) and label == EXPECTED[index]:
            if label != "08_certify":
                require((rc == 0 and kind == "PASS_COMMAND_ONLY") or
                        (rc != 0 and kind == "FAILED_OR_BLOCKED"), "contradictory step status")
            else:
                require((rc == 2 and kind == "EXPECTED_PROVISIONING_BLOCKER_NOT_APP_PASS") or
                        (rc == 0 and kind == "COMMAND_SUCCESS_NOT_APP_PASS") or
                        (rc == 6 and kind == "LOGGING_FAILED") or
                        (rc != 0 and kind == "UNEXPECTED_FAILURE"), "invalid certify classification")
        elif index > 0 and index == len(lines) - 2 and label in (
            "02_sync_lock_integrity", f"{EXPECTED[index - 1]}_source_integrity"):
            require(rc == 5 and kind in ("LOCK_DRIFT", "SOURCE_DRIFT"),
                    "invalid integrity stop record")
        else:
            raise ReceiptError("missing, reordered, or extra qualification step")
        require(not records or records[-1][1] == 0 and records[-1][0] != "08_certify",
                "command continued after first failure")
        records.append((label, rc, kind))
    return records


def review(source: Path, expected: str, evidence: Path) -> dict[str, object]:
    expected_files = source_archive(source, expected)
    require(evidence.is_dir() and not evidence.is_symlink(), "evidence directory unavailable")
    post = key_values(file_at(evidence, "M209_POST_RUN_PROVENANCE.txt"))
    pre_bytes = file_at(evidence, "M209_PRE_RUN_SOURCE.sha256")
    after_bytes = file_at(evidence, "M209_POST_RUN_SOURCE.sha256")
    pre, after = manifest(pre_bytes), manifest(after_bytes)
    steps_bytes = file_at(evidence, "M209_STEPS.tsv")
    steps = steps_table(steps_bytes)
    # All attempted-owner logs are byte-bound. Last integrity pseudo-steps have
    # no independent command/log and therefore no additional log entry.
    log_bytes = file_at(evidence, "M209_LOGS.sha256")
    logs = manifest_logs(log_bytes)
    expected_logs = {label + ".log" for label, _rc, _kind in steps if label in EXPECTED}
    require(set(logs) == expected_logs, "missing, uncommitted or extra command log")
    observed_logs = {p.name for p in evidence.iterdir() if p.name.endswith(".log")}
    require(observed_logs == expected_logs, "uncommitted command log exists on disk")
    for log_name, digest in logs.items():
        require(sha(file_at(evidence, log_name)) == digest,
                f"command log bytes changed: {log_name}")
    require(pre == expected_files, "PRE source does not match exact candidate bytes")
    require(after == expected_files, "POST source differs from exact candidate bytes")
    require(post.get("schema") == "oh-goon.native-wsl-qualification-provenance/v2",
            "unknown provenance schema")
    for key, actual in {
        "pre_run_source_manifest_sha256": sha(pre_bytes),
        "post_run_source_manifest_sha256": sha(after_bytes),
        "command_exit_receipts_sha256": sha(steps_bytes),
        "canonical_members_integrity_same_attempt": "PASS",
        "locks_byte_identical_same_attempt": "PASS",
        "app_pass": "NOT_ESTABLISHED",
        "gate_a": "NOT_RUN",
        "physical_lace": "NOT_ESTABLISHED",
        "release": "BLOCKED",
        "wsl_detected": "true",
        "command_exit_receipts": "M209_STEPS.tsv",
        "command_log_receipts": "M209_LOGS.sha256",
        "command_log_receipts_sha256": sha(log_bytes),
        "command_log_integrity": "PASS",
        "authenticated_replay": "NOT_AUTHORIZED",
    }.items():
        require(post.get(key) == actual, f"invalid {key}")
    require(post.get("backend_uv_lock_before") == expected_files.get("backend/uv.lock")
            == post.get("backend_uv_lock_after"), "backend lock is unbound")
    require(post.get("frontend_npm_lock_before") == expected_files.get("frontend/package-lock.json")
            == post.get("frontend_npm_lock_after"), "frontend lock is unbound")
    require(post.get("last_step") == steps[-1][0]
            and post.get("last_step_exit") == str(steps[-1][1]),
            "last step does not match command receipts")
    require(post.get("runner_exit") is not None and post["runner_exit"].isdigit(),
            "missing runner exit")
    require(int(post["runner_exit"]) != 0, "standalone qualifier cannot claim APP success")
    if steps[-1][0] == "08_certify":
        kind = steps[-1][2]
        require(post.get("certify_classification") == {
            "EXPECTED_PROVISIONING_BLOCKER_NOT_APP_PASS": "EXPECTED_PROVISIONING_BLOCKER_OBSERVED_NOT_APP_PASS",
            "COMMAND_SUCCESS_NOT_APP_PASS": "COMMAND_EXIT_0_APP_PROOF_UNVERIFIED",
            "LOGGING_FAILED": "LOGGING_FAILED",
            "UNEXPECTED_FAILURE": "UNEXPECTED_CERTIFY_FAILURE",
        }[kind], "certification classification mismatch")
        required_phase, required_exit = {
            "EXPECTED_PROVISIONING_BLOCKER_NOT_APP_PASS": ("BLOCKED_AT_DEPLOYMENT_ENROLLMENT", "2"),
            "COMMAND_SUCCESS_NOT_APP_PASS": ("BLOCKED_PENDING_INDEPENDENT_APP_PASS", "2"),
            "LOGGING_FAILED": ("STOPPED_AT_CERTIFY_LOGGING", "6"),
            "UNEXPECTED_FAILURE": ("STOPPED_AT_CERTIFY", str(steps[-1][1])),
        }[kind]
    else:
        require(post.get("certify_classification") == "NOT_RUN",
                "certification classification inferred without certification")
        if steps[-1][2] == "LOCK_DRIFT":
            required_phase, required_exit = "STOPPED_AT_LOCK_DRIFT", "5"
        elif steps[-1][2] == "SOURCE_DRIFT":
            required_phase, required_exit = "STOPPED_AT_SOURCE_DRIFT", "5"
        else:
            required_phase = "STOPPED_AT_" + steps[-1][0]
            required_exit = str(steps[-1][1])
    require(post.get("qualification_phase") == required_phase
            and post.get("runner_exit") == required_exit,
            "qualifier phase/exit inconsistent with recorded stop")
    return {
        "schema": "oh-goon.a1-native-receipt-review/v2",
        "status": "CONSISTENT_DIAGNOSTIC_NOT_NATIVE_ATTESTATION",
        "candidate_sha256": expected,
        "source_files": len(expected_files),
        "steps_observed": len(steps),
        "logs_verified": len(logs),
        "last_step": steps[-1][0],
        "last_classification": steps[-1][2],
        "app_pass": "NOT_ESTABLISHED",
        "gate_a": "NOT_ESTABLISHED",
        "real_deployment_custody": "NOT_ATTESTED",
        "physical_lace": "NOT_ESTABLISHED",
        "release": "BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = review(args.source_zip, args.source_sha256, args.evidence_dir)
    except (ReceiptError, OSError, UnicodeError, ValueError, EOFError, BadZipFile) as exc:
        print(json.dumps({"schema": "oh-goon.a1-native-receipt-review/v2",
                          "status": "REJECTED_INCOMPLETE_OR_INCONSISTENT",
                          "reason": str(exc), "app_pass": "NOT_ESTABLISHED"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
