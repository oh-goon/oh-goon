from oh_goon.core.operation_economics import economics_receipt


def test_operation_identity_is_semantic_and_metrics_are_observational() -> None:
    first = economics_receipt(
        operation="certification.supply.verify-bytes",
        authority_owner="Oh-Goon certification dependency supply",
        artifact_identity="sha256:" + "a" * 64,
        outcome="PERFORMED",
        reason="first observation",
        bytes_hashed=10,
        bytes_read=10,
        strong_proof_performed=True,
        elapsed_seconds=1.5,
    )
    second = economics_receipt(
        operation=first.operation,
        authority_owner=first.authority_owner,
        artifact_identity=first.artifact_identity,
        outcome="REUSED",
        reason="different observation",
        bytes_hashed=20,
        bytes_read=20,
        reused_bytes=10,
        strong_proof_performed=True,
        elapsed_seconds=9.0,
    )

    assert first.operation_identity == second.operation_identity
    assert first.as_dict()["elapsed_seconds"] == 1.5
    assert second.as_dict()["reused_bytes"] == 10
