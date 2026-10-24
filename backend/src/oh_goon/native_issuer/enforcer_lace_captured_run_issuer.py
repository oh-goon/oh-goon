"""M158 non-live source issuer bound to one *durable* Fleet RUN_ADMITTED.

The operational Journal owns the Run's selected repositories, Plan admission
and canonical roots. No caller-supplied repository set or paths are accepted.
This is an audit/candidate issuance boundary, not physical admission: actual
Enforcer admission must co-publish the same captured set atomically in Phase 4.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import IssuerEvidence
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.errors import RunNotFound
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunStatus
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
from oh_goon.native_issuer.enforcer_lace_run_source_issuer import (
    issue_observed_run_repository_source_identity,
)
from oh_goon.runtime.journal import Journal


def _read_original_admission(
    journal: Journal,
    run_id: str,
    *,
    prework: PreworkRunCapture | None = None,
) -> tuple[RunAdmittedEvent, int]:
    """Require a sole queued Run admission, never a stale/history replay."""
    if prework is not None:
        if type(prework) is not PreworkRunCapture:
            raise TrustProtocolError("trusted native pre-work capture owner required")
        return prework.read(journal=journal, run_id=run_id)
    if not isinstance(journal, Journal) or not isinstance(run_id, str) or not run_id:
        raise TrustProtocolError("durable Fleet Journal and exact Run identity required")
    try:
        batch = journal.read_view().run_events(run_id)
    except (ValueError, OSError, KeyError, RunNotFound, RuntimeError) as exc:
        raise TrustProtocolError("durable Run admission is unavailable") from exc
    if (
        len(batch.events) != 1
        or batch.next_sequence != 1
        or batch.run.status is not FleetRunStatus.QUEUED
        or batch.run.fleet_run_id != run_id
    ):
        raise TrustProtocolError("Run is not at its sole committed admission cut")
    event = batch.events[0]
    if (
        not isinstance(event, RunAdmittedEvent)
        or event.event_type is not FleetRunEventType.RUN_ADMITTED
        or event.sequence != 1
        or event.fleet_run_id != run_id
        or not isinstance(event.target, FleetPlanTarget)
        or len(event.repository_admissions) < 2
    ):
        raise TrustProtocolError("Run admission lacks multi-repository Fleet Plan authority")
    # Revalidate the entire persisted immutable model; never trust partial objects.
    try:
        validated = RunAdmittedEvent.model_validate(
            event.model_dump(mode="python", exclude_computed_fields=True)
        )
    except (ValueError, TypeError) as exc:
        raise TrustProtocolError("invalid stored RUN_ADMITTED authority") from exc
    if validated != event:
        raise TrustProtocolError("stored Run admission is noncanonical")
    return validated, batch.journal_revision


def issue_observed_captured_run_source(
    *,
    journal: Journal,
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
    prework: PreworkRunCapture | None = None,
) -> IssuerEvidence:
    """Issue only for exact repositories *read from* the one operational Journal.

    Cannot be used after Run start/cancel/terminal or with an arbitrary caller
    selection. The second Journal read fences observed changes during signing.
    This does not replace one-transaction live admission or LACE source pinning.
    """
    contract.verify()
    run_id = contract.fields["run_id"]
    admitted, revision = _read_original_admission(journal, run_id, prework=prework)
    selected = tuple(item.repository_id for item in admitted.repository_admissions)
    captured = {target.repository_id: target.path for target in admitted.targets}
    target = admitted.target
    if not isinstance(target, FleetPlanTarget):
        raise TrustProtocolError("durable Run source selection requires a Fleet Plan target")
    if (
        set(captured) != set(selected)
        or len(captured) != len(admitted.targets)
        or target.plan.plan_id != admitted.repository_admissions[0].plan_id
    ):
        raise TrustProtocolError("durable Run repository scope differs from admitted Plan")
    evidence = issue_observed_run_repository_source_identity(
        repositories=captured,
        admissions=admitted.repository_admissions,
        selected_repository_ids=selected,
        contract=contract,
        issuer_id=issuer_id,
        signing_key=signing_key,
    )
    after, later_revision = _read_original_admission(journal, run_id, prework=prework)
    if later_revision != revision or after != admitted:
        raise TrustProtocolError("durable admission changed while issuing Run evidence")
    return evidence


REPOSITORY_CAPTURE_DOMAIN = "oh-goon/fleet-run-captured-repository-identity/v1"
REPOSITORY_FIELD = "repository_identity"


def admitted_run_repository_capture_identity(admitted: RunAdmittedEvent) -> str:
    """Canonical projection of original Run target roots and admitted subjects."""
    from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest

    if not isinstance(admitted, RunAdmittedEvent) or not isinstance(
        admitted.target, FleetPlanTarget
    ):
        raise TrustProtocolError("durable Fleet Plan RUN_ADMITTED required")
    try:
        verified = RunAdmittedEvent.model_validate(
            admitted.model_dump(mode="python", exclude_computed_fields=True)
        )
    except (ValueError, TypeError) as exc:
        raise TrustProtocolError("invalid captured repository authority") from exc
    if verified != admitted or len(admitted.repository_admissions) < 2:
        raise TrustProtocolError("canonical multi-repository capture required")
    by_id = {target.repository_id: target for target in admitted.targets}
    selected = tuple(item.repository_id for item in admitted.repository_admissions)
    if len(by_id) != len(admitted.targets) or set(by_id) != set(selected):
        raise TrustProtocolError("captured repository subjects differ from durable admissions")
    return digest(
        REPOSITORY_CAPTURE_DOMAIN,
        canonical_bytes(
            {
                "run_id": admitted.fleet_run_id,
                "plan_id": admitted.target.plan.plan_id,
                "repositories": [
                    {
                        "repository_id": item.repository_id,
                        "canonical_root": str(by_id[item.repository_id].path),
                        "subject_instance_id": item.source_authority.subject_instance_id,
                        "source_authority_id": item.source_authority.authority_id,
                        "repository_admission_id": item.admission_id,
                    }
                    for item in admitted.repository_admissions
                ],
            }
        ),
    )


def issue_observed_captured_run_repository_identity(
    *,
    journal: Journal,
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
    prework: PreworkRunCapture | None = None,
) -> IssuerEvidence:
    """Sign Run repository capture with its *separate* repository issuer key.

    This does not accept source issuer keys or caller-provided roots. The only
    source of membership and canonical paths is operational RUN_ADMITTED.
    """
    from oh_goon.core.enforcer_lace_issuer_evidence import ISSUER_PROTOCOL, SIGNATURE_DOMAIN
    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.native_issuer import enforcer_lace_repository_snapshot_issuer as native

    contract.verify()
    if (
        not isinstance(signing_key, Ed25519PrivateKey)
        or not isinstance(issuer_id, str)
        or not issuer_id
        or not issuer_id.isascii()
        or any(c in issuer_id for c in "\x00\n/")
    ):
        raise TrustProtocolError("independent repository signing authority required")
    values = contract.fields
    admitted, revision = _read_original_admission(journal, values["run_id"], prework=prework)
    identity = admitted_run_repository_capture_identity(admitted)
    if values[REPOSITORY_FIELD] != identity:
        raise TrustProtocolError("Run repository identity differs from captured durable admission")
    selected = {item.repository_id: item for item in admitted.repository_admissions}

    def observe() -> None:
        for target in admitted.targets:
            item = selected[target.repository_id]
            root = target.path
            if (
                not root.is_absolute()
                or root.is_symlink()
                or not root.is_dir()
                or root.resolve() != root
            ):
                raise TrustProtocolError("unsafe captured native repository root")
            try:
                subject = native.repository_subject_instance_id(root)
                snapshot = native.source_snapshot_identity(root)
            except (OSError, ValueError, ImportError) as exc:
                raise TrustProtocolError(
                    "captured repository owner observation unavailable"
                ) from exc
            expected = item.source_authority.source_snapshot
            if subject != item.source_authority.subject_instance_id or (
                snapshot.sha256,
                snapshot.byte_count,
                snapshot.entry_count,
            ) != (expected.sha256, expected.byte_count, expected.entry_count):
                raise TrustProtocolError("captured repository no longer matches native owner")

    observe()
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": REPOSITORY_FIELD,
            "run_id": values["run_id"],
            "contract_identity": contract.identity,
            "identity": identity,
            "executor_generation": values["executor_generation"],
        }
    )
    observe()
    after, later_revision = _read_original_admission(journal, values["run_id"], prework=prework)
    if later_revision != revision or after != admitted:
        raise TrustProtocolError("durable captured repositories changed during signing")
    message = SIGNATURE_DOMAIN + REPOSITORY_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(REPOSITORY_FIELD, issuer_id, payload, signing_key.sign(message).hex())
