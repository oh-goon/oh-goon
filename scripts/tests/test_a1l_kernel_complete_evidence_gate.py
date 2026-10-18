"""A1L: a pytest exit zero without all actual kernel cases is NOT proof."""
from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from oh_goon.repository_tooling import a1_kernel_custody_receipt as module

ROOT = Path(__file__).resolve().parents[2]


def _attempt(tmp_path: Path, **changes):
    directory = tmp_path / "proof"
    directory.mkdir()
    suite = ET.Element("testsuite", tests="3", failures="0", errors="0", skipped="0")
    for name in sorted(module.EXPECTED):
        case = ET.SubElement(suite, "testcase", classname="test_a1h_real_linux_process_custody", name=name)
        if changes.get("skip") == name:
            ET.SubElement(case, "skipped")
    if changes.get("drop"):
        suite.remove(list(suite)[0])
    if changes.get("duplicate"):
        ET.SubElement(suite, "testcase", classname="test_a1h_real_linux_process_custody", name=list(suite)[0].get("name"))
    if changes.get("wrong_owner"):
        list(suite)[0].set("classname", "test_unrelated")
    if changes.get("bad_count"):
        suite.set("tests", "2")
    junit = directory / "junit.xml"
    junit.write_bytes(ET.tostring(suite, encoding="utf-8", xml_declaration=True))
    log = directory / "raw.log"
    log.write_text("original native pytest run; not deployment attestation\n")
    return junit, log


def test_three_real_kernel_cases_retain_distinct_diagnostic_boundary(tmp_path: Path):
    junit, log = _attempt(tmp_path)
    result = module.verify(junit, log, 0)
    assert result["test_count"] == 3
    assert len(result["junit_sha256"]) == len(result["log_sha256"]) == 64
    assert result["real_deployment_custody"] == "NOT_ATTESTED"
    assert result["app_pass"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("mutation", ["skip", "drop", "duplicate", "wrong_owner", "bad_count", "failed_exit", "linked", "missing_log", "malformed_xml"])
def test_skipped_replaced_or_incomplete_proof_rejected(tmp_path: Path, mutation: str):
    args = {mutation: next(iter(module.EXPECTED))} if mutation == "skip" else {mutation: True}
    junit, log = _attempt(tmp_path, **args)
    if mutation == "linked":
        junit.rename(junit.with_suffix(".raw"))
        junit.symlink_to(junit.with_suffix(".raw"))
    elif mutation == "missing_log":
        log.unlink()
    elif mutation == "malformed_xml":
        junit.write_text("<testsuite>")
    with pytest.raises((module.CustodyReceiptError, OSError)):
        module.verify(junit, log, 1 if mutation == "failed_exit" else 0)


def test_native_kernel_target_uses_complete_evidence_gate():
    makefile = (ROOT / "Makefile").read_text()
    target = makefile.split("\na1-native-kernel-custody-test:\n", 1)[1].split("\n\n", 1)[0]
    assert "scripts/a1_kernel_custody.sh" in target
    assert "A1_KERNEL_EVIDENCE_DIR" in target
    script = (ROOT / "scripts/a1_kernel_custody.sh").read_text()
    assert "--junitxml" in script and "--pytest-exit" in script
    assert "oh-goon-a1-kernel-custody-receipt" in script
    assert "scripts/qualify_native_wsl.sh" not in target


def test_root_host_cannot_reuse_existing_evidence_directory(tmp_path: Path):
    """One attempt cannot append receipts to previous attempts."""
    proof = tmp_path / "already-exists"
    proof.mkdir()
    result = subprocess.run(["bash", "scripts/a1_kernel_custody.sh", str(proof),
                             "scripts/tests/test_a1h_real_linux_process_custody.py"],
                            cwd=ROOT, capture_output=True, text=True,
                            env=os.environ.copy(), check=False, timeout=10)
    assert result.returncode != 0
    assert not list(proof.iterdir())
