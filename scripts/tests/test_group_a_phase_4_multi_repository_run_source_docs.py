"""M157 docs and architecture regressions for one Run source authority."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_run_source_issuer.py"


def test_run_issuer_uses_existing_per_repository_admission_owner() -> None:
    source = OWNER.read_text()
    tree = ast.parse(source)
    functions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "admitted_run_repository_source_identity" in functions
    assert "issue_observed_run_repository_source_identity" in functions
    assert "PlanRepositoryAdmissionEvidence" in source
    assert "native.source_snapshot_identity" in source
    assert "native.repository_subject_instance_id" in source
    assert "hashlib" not in source and "identify_path" not in source


def test_live_execution_and_enforcer_do_not_import_private_key_issuer() -> None:
    for path in (
        "backend/src/oh_goon/services/fleet_admission.py",
        "backend/src/oh_goon/services/fleet_run_lifecycle.py",
        "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py",
        "backend/src/oh_goon/runtime/enforcer_lace_journal.py",
    ):
        assert "enforcer_lace_run_source_issuer" not in (ROOT / path).read_text()
