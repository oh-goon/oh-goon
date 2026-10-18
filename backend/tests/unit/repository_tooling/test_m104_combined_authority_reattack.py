from __future__ import annotations

from oh_goon.repository_tooling import repository_understanding_material as material


def test_repository_understanding_snapshot_accepts_all_current_evidence_ranges() -> None:
    """M104: source refactors must not leave certification evidence outside current bytes."""

    snapshot = material.exam_snapshot()
    identities = [
        identity
        for question in snapshot["questions"]
        for identity in question["secret"]["evidence_reference_identities"]
    ]

    assert identities
    assert all(identity["start_line"] >= 1 for identity in identities)
    assert all(identity["end_line"] >= identity["start_line"] for identity in identities)
    assert all(identity["source_sha256"].startswith("sha256:") for identity in identities)
    assert all(identity["range_sha256"].startswith("sha256:") for identity in identities)
