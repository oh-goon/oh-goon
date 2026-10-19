"""A1e: provenance must bind each entered native owner's raw log bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from oh_goon.repository_tooling.a1_native_receipt_review import ReceiptError, review
from test_m209_native_wsl_qualification import _fixture, _run


def _attempt(tmp_path: Path, *, fail_step: str | None = None) -> tuple[Path, str, Path]:
    root, env = _fixture(tmp_path)
    with ZipFile(tmp_path / "candidate.zip", "w") as archive:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(root).as_posix())
    candidate = tmp_path / "candidate.zip"
    if fail_step:
        env["M209_TEST_FAIL_STEP"] = fail_step
    evidence = tmp_path / "evidence"
    result = _run(root, env, evidence)
    assert result.returncode == (17 if fail_step else 2), result.stdout + result.stderr
    return candidate, hashlib.sha256(candidate.read_bytes()).hexdigest(), evidence


def test_full_blocked_attempt_binds_all_nine_logs(tmp_path: Path) -> None:
    source, sha, evidence = _attempt(tmp_path)
    result = review(source, sha, evidence)
    assert result["logs_verified"] == 9
    assert result["app_pass"] == "NOT_ESTABLISHED"
    assert result["status"] == "CONSISTENT_DIAGNOSTIC_NOT_NATIVE_ATTESTATION"
    post = (evidence / "M209_POST_RUN_PROVENANCE.txt").read_text()
    assert "schema=oh-goon.native-wsl-qualification-provenance/v2" in post
    assert "command_log_integrity=PASS" in post


def test_early_failure_binds_only_entered_owners(tmp_path: Path) -> None:
    source, sha, evidence = _attempt(tmp_path, fail_step="verify-p0")
    result = review(source, sha, evidence)
    assert result["logs_verified"] == 6
    assert result["last_step"] == "05_verify_p0"
    assert not (evidence / "08_certify.log").exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "change_log",
        "delete_log",
        "extra_log",
        "swap_logs",
        "corrupt_manifest",
        "delete_manifest",
        "duplicate_record",
        "unknown_log",
        "change_digest",
        "replace_log_symlink",
        "old_schema",
        "fake_log_integrity",
        "forge_replay",
        "truncated_manifest",
        "orphaned_log_manifest",
        "untracked_extra_log",
    ],
)
def test_log_evidence_tampering_fails_closed(tmp_path: Path, tamper: str) -> None:
    source, sha, evidence = _attempt(tmp_path)
    log = evidence / "08_certify.log"
    manifest = evidence / "M209_LOGS.sha256"
    post = evidence / "M209_POST_RUN_PROVENANCE.txt"
    if tamper == "change_log":
        log.write_bytes(log.read_bytes() + b"\nforged success\n")
    elif tamper == "delete_log":
        log.unlink()
    elif tamper == "extra_log":
        (evidence / "09_extra.log").write_text("unreported")
        # Even if the attacker supplies a matching record, no extra execution is allowed.
        manifest.write_text(manifest.read_text() + "0" * 64 + "  09_extra.log\n")
    elif tamper == "untracked_extra_log":
        (evidence / "09_ignored.log").write_text("hidden attempt")
    elif tamper == "swap_logs":
        a, b = evidence / "01_source_transport_clean.log", evidence / "02_sync.log"
        a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
        a.write_bytes(b_bytes)
        b.write_bytes(a_bytes)
    elif tamper == "corrupt_manifest":
        manifest.write_text("not a hash manifest\n")
    elif tamper == "delete_manifest":
        manifest.unlink()
    elif tamper == "duplicate_record":
        manifest.write_text(
            manifest.read_text() + manifest.read_text().splitlines()[0] + "\n"
        )
    elif tamper == "unknown_log":
        manifest.write_text(
            manifest.read_text().replace("08_certify.log", "09_unknown.log")
        )
    elif tamper == "change_digest":
        manifest.write_text("0" * 64 + manifest.read_text()[64:])
    elif tamper == "replace_log_symlink":
        log.unlink()
        log.symlink_to("M209_STEPS.tsv")
    elif tamper == "old_schema":
        post.write_text(
            post.read_text().replace(
                "qualification-provenance/v2", "qualification-provenance/v1"
            )
        )
    elif tamper == "fake_log_integrity":
        post.write_text(
            post.read_text().replace(
                "command_log_integrity=PASS", "command_log_integrity=UNKNOWN"
            )
        )
    elif tamper == "forge_replay":
        post.write_text(
            post.read_text().replace(
                "authenticated_replay=NOT_AUTHORIZED", "authenticated_replay=AUTHORIZED"
            )
        )
    elif tamper == "truncated_manifest":
        manifest.write_bytes(manifest.read_bytes()[:-1])
    elif tamper == "orphaned_log_manifest":
        lines = manifest.read_text().splitlines()
        manifest.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(ReceiptError):
        review(source, sha, evidence)


def test_log_inventory_is_not_host_attestation_even_when_self_consistent(
    tmp_path: Path,
) -> None:
    """A self-authored log+hash+provenance chain still cannot establish a trusted host."""
    source, sha, evidence = _attempt(tmp_path)
    path = evidence / "08_certify.log"
    path.write_bytes(path.read_bytes() + b"\nlocal diagnostic annotation\n")
    manifest = evidence / "M209_LOGS.sha256"
    records = manifest.read_text().splitlines()
    records[-1] = hashlib.sha256(path.read_bytes()).hexdigest() + "  08_certify.log"
    manifest.write_text("\n".join(records) + "\n")
    post = evidence / "M209_POST_RUN_PROVENANCE.txt"
    lines = post.read_text().splitlines()
    lines = [
        "command_log_receipts_sha256="
        + hashlib.sha256(manifest.read_bytes()).hexdigest()
        if line.startswith("command_log_receipts_sha256=")
        else line
        for line in lines
    ]
    post.write_text("\n".join(lines) + "\n")
    result = review(source, sha, evidence)
    assert result["real_deployment_custody"] == "NOT_ATTESTED"
    assert result["app_pass"] == "NOT_ESTABLISHED"
