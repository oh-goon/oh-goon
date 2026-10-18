"""A1d: require exact source+receipts, never promote CLI evidence to APP PASS."""
from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from oh_goon.repository_tooling.a1_native_receipt_review import ReceiptError, review
from test_m209_native_wsl_qualification import _fixture, _run


def _realistic_attempt(tmp_path: Path) -> tuple[Path, str, Path]:
    source, env = _fixture(tmp_path)
    archive = tmp_path / "candidate.zip"
    with ZipFile(archive, "w") as zip_file:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                zip_file.write(file, file.relative_to(source).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    evidence = tmp_path / "evidence"
    assert _run(source, env, evidence).returncode == 2
    return archive, digest, evidence


def test_exact_blocked_receipts_are_diagnostic_not_app_proof(tmp_path: Path) -> None:
    archive, digest, evidence = _realistic_attempt(tmp_path)
    result = review(archive, digest, evidence)
    assert result["status"] == "CONSISTENT_DIAGNOSTIC_NOT_NATIVE_ATTESTATION"
    assert result["app_pass"] == "NOT_ESTABLISHED"
    assert result["real_deployment_custody"] == "NOT_ATTESTED"
    assert result["last_step"] == "08_certify"


@pytest.mark.parametrize("change", [
    "wrong_digest", "mutated_zip", "source_byte", "delete_member", "manifest_hash",
    "duplicate_provenance", "missing_log", "forge_app", "duplicate_step",
    "reorder_steps", "fake_certification", "mutate_lock", "symlink_log", "phase_forgery",
])
def test_receipt_attacks_fail_closed(tmp_path: Path, change: str) -> None:
    archive, digest, evidence = _realistic_attempt(tmp_path)
    if change == "wrong_digest":
        digest = "0" * 64
    elif change == "mutated_zip":
        archive.write_bytes(archive.read_bytes() + b"unqualified")
    elif change == "source_byte":
        path = evidence / "M209_PRE_RUN_SOURCE.sha256"
        path.write_bytes(path.read_bytes().replace(b"backend/uv.lock", b"backend/no.lock"))
    elif change == "delete_member":
        path = evidence / "M209_POST_RUN_SOURCE.sha256"
        path.write_text("\n".join(path.read_text().splitlines()[1:]) + "\n")
    elif change == "manifest_hash":
        path = evidence / "M209_PRE_RUN_SOURCE.sha256"
        path.write_bytes(path.read_bytes().replace(b"a", b"b", 1))
    elif change == "duplicate_provenance":
        with (evidence / "M209_POST_RUN_PROVENANCE.txt").open("a") as f:
            f.write("app_pass=NOT_ESTABLISHED\n")
    elif change == "missing_log":
        (evidence / "03_native_issuer_wheel.log").unlink()
    elif change == "forge_app":
        path = evidence / "M209_POST_RUN_PROVENANCE.txt"
        path.write_text(path.read_text().replace("app_pass=NOT_ESTABLISHED", "app_pass=PASS"))
    elif change == "duplicate_step":
        with (evidence / "M209_STEPS.tsv").open("a") as f:
            f.write("08_certify\t2\tEXPECTED_PROVISIONING_BLOCKER_NOT_APP_PASS\n")
    elif change == "reorder_steps":
        path = evidence / "M209_STEPS.tsv"
        lines = path.read_text().splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        path.write_text("\n".join(lines) + "\n")
    elif change == "fake_certification":
        path = evidence / "M209_POST_RUN_PROVENANCE.txt"
        path.write_text(path.read_text().replace(
            "certify_classification=EXPECTED_PROVISIONING_BLOCKER_OBSERVED_NOT_APP_PASS",
            "certify_classification=COMMAND_EXIT_0_APP_PROOF_UNVERIFIED"))
    elif change == "mutate_lock":
        path = evidence / "M209_POST_RUN_PROVENANCE.txt"
        old_line = next(line for line in path.read_text().splitlines()
                        if line.startswith("backend_uv_lock_after="))
        path.write_text(path.read_text().replace(old_line, "backend_uv_lock_after=" + "0"*64))
    elif change == "phase_forgery":
        path = evidence / "M209_POST_RUN_PROVENANCE.txt"
        path.write_text(path.read_text().replace("qualification_phase=BLOCKED_AT_DEPLOYMENT_ENROLLMENT",
                                                 "qualification_phase=COMPLETED"))
    elif change == "symlink_log":
        path = evidence / "08_certify.log"
        path.unlink()
        path.symlink_to("M209_STEPS.tsv")
    with pytest.raises(ReceiptError):
        review(archive, digest, evidence)
