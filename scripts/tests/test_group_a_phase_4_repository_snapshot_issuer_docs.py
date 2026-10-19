"""M156 source-backed owner/negative evidence: one native snapshot owner."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISSUER = (
    ROOT
    / "backend/src/oh_goon/native_issuer/enforcer_lace_repository_snapshot_issuer.py"
)
OWNER = ROOT / "backend/src/oh_goon/integrations/source_snapshot.py"


def test_issuer_uses_existing_native_owner_and_no_second_hash_engine() -> None:
    source = ISSUER.read_text()
    parsed = ast.parse(source)
    names = {
        node.name for node in ast.walk(parsed) if isinstance(node, ast.FunctionDef)
    }
    assert "issue_observed_repository_source_identity" in names
    assert "admitted_repository_source_identity" in names
    assert (
        "from oh_goon.integrations.source_snapshot import source_snapshot_identity"
        in source
    )
    assert (
        "from oh_goon.core.product_identity import repository_subject_instance_id"
        in source
    )
    assert "hashlib" not in source and "identify_path" not in source
    assert (
        "from oh_goon.integrations.hashmarks.identity import identify_path"
        in OWNER.read_text()
    )


def test_private_signer_is_not_imported_into_runtime_or_enforcer() -> None:
    for relative in (
        "backend/src/oh_goon/services/fleet_admission.py",
        "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py",
        "backend/src/oh_goon/runtime/enforcer_lace_journal.py",
    ):
        assert (
            "enforcer_lace_repository_snapshot_issuer"
            not in (ROOT / relative).read_text()
        )
