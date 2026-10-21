"""P1B27B2B2A native evidence: selected native coverage must match ring owner."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)

ROOT = Path(__file__).resolve().parents[2]
QUALIFIER = ROOT / "scripts/qualify_b12_development_wsl.sh"
MODULES = (
    "test_p1b27b1_native_mount_roster",
    "test_p1b27b2a_signed_native_sources",
    "test_p1b27b2b2a_pinned_bind_source",
    "test_p1b27b2b3a_kernel_mount_coverage",
    "test_p1b27b2b3b1_mount_fd_lifecycle",
)


def test_normal_native_ring_contract_has_one_owner_and_complete_requirements() -> None:
    assert not check_qualification_environment_contract(ROOT)
    source = QUALIFIER.read_text()
    physical = source.split('if [[ "$name" == PHYSICAL_OBSERVER* ]]; then')[1].split(
        'if [[ "$name" == AGENT_PREWORK* ]]; then'
    )[0]
    agent = source.split('if [[ "$name" == AGENT_PREWORK* ]]; then')[1].split(
        'qualify_host_sample "${name}_BEFORE"'
    )[0]
    observer_tests = source.split("observer_tests=()")[1].split(
        'run_ring "PHYSICAL_OBSERVER${suffix}"'
    )[0]
    for module in MODULES:
        assert physical.count(f"--require-module {module}") == 1
        assert (
            observer_tests.count(f"observer_tests+=(tests/unit/runtime/{module}.py)")
            == 1
        )
        assert f"--require-module {module}" not in agent


@pytest.mark.parametrize(
    "mutation",
    [
        "require-in-agent",
        "missing-requirement",
        "missing-selection",
        "wrong-marker",
        "unconditional-extra",
    ],
)
def test_x0_rejects_ring_mismatch_before_native_execution(
    tmp_path: Path, mutation: str
) -> None:
    source = QUALIFIER.read_text()
    original = "test_p1b27b2b2a_pinned_bind_source"
    requirement = f"required_modules+=(--require-module {original})"
    selection = f"observer_tests+=(tests/unit/runtime/{original}.py)"
    if mutation == "require-in-agent":
        anchor = '  if [[ "$name" == AGENT_PREWORK* ]]; then'
        assert anchor in source
        source = source.replace(anchor, anchor + f"\n    {requirement}", 1)
    elif mutation == "missing-requirement":
        source = source.replace(requirement, "true", 1)
    elif mutation == "missing-selection":
        source = source.replace(selection, "true", 1)
    elif mutation == "wrong-marker":
        # Moved under a different predecessor manifest: must not count as coverage.
        old = (
            "if [[ -f .checkpoint/P1B27B2B2A_SOURCE.sha256 ]]; then\n      "
            + requirement
        )
        new = (
            "if [[ -f .checkpoint/P1B27B2B2B_SOURCE.sha256 ]]; then\n      "
            + requirement
        )
        assert old in source
        source = source.replace(old, new, 1)
    elif mutation == "unconditional-extra":
        anchor = '  if [[ "$name" == AGENT_PREWORK* ]]; then'
        assert anchor in source
        source = source.replace(anchor, f"  {requirement}\n" + anchor, 1)
    qualifier = tmp_path / "scripts/qualify_b12_development_wsl.sh"
    qualifier.parent.mkdir(parents=True)
    qualifier.write_text(source)
    # Reuse original Makefile and exact checkpoint markers; no separate checker.
    (tmp_path / "Makefile").write_bytes((ROOT / "Makefile").read_bytes())
    issue_messages = [
        issue.message for issue in check_qualification_environment_contract(tmp_path)
    ]
    assert any("QENV1 P1B27B2B2A" in message for message in issue_messages), (
        issue_messages
    )
