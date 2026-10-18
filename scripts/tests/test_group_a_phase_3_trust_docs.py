"""Phase 3 anti-drift: source, QA, owner docs and non-live boundary agree."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_phase_3_trust_and_journal_do_not_start_execution_or_duplicate_resolver():
    base = ROOT / "backend/src/oh_goon"
    for name in ["core/enforcer_lace_trust.py", "runtime/enforcer_lace_journal.py"]:
        ast.parse((base / name).read_text(encoding="utf-8"))
    new_journal = (base / "runtime/enforcer_lace_journal.py").read_text(
        encoding="utf-8"
    )
    assert "BEGIN IMMEDIATE" in new_journal
    assert "RUN_ADMITTED" in new_journal
    assert "JOURNAL_UNRESOLVED" in new_journal
    # B12D2C3: original Fleet-owned, epoch-fenced LACE proof now legitimately
    # imports this journal. Keep an exact frozen census; unrelated CLI, Direct,
    # remote provider and startup paths must not become alternative publishers.
    callers = []
    for path in base.rglob("*.py"):
        if path.name == "enforcer_lace_journal.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "oh_goon.runtime.enforcer_lace_journal"
            ):
                callers.append(str(path.relative_to(base)))
    assert set(callers) == {
        "services/fleet_admission.py",
        "services/fleet_run_lifecycle.py",
        "services/fleet_run_execution.py",
        "services/fleet_local_lace_takeover.py",
        "services/fleet_recovery_issuer_fence.py",
        "runtime/enforcer_lace_orphan_subject.py",
        "runtime/enforcer_lace_local_resource_intent.py",
        "runtime/enforcer_lace_takeover_recovery.py",
        "runtime/enforcer_lace_crash_reconciliation.py",
        "runtime/enforcer_lace_fleet_resource_owner.py",
        "runtime/enforcer_lace_native_cleanup.py",
        "runtime/enforcer_lace_cleanup_attempt.py",
        "runtime/enforcer_lace_admitted_signer_epoch.py",
        "runtime/enforcer_lace_takeover_preflight.py",
        "runtime/enforcer_lace_restart_transfer.py",
    }
