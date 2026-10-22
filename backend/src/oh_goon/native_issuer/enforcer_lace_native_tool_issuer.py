"""M160: non-live native tool evidence for one durably admitted Fleet Run.

Selection belongs to each captured repository's active Plan. Resolution belongs to
StrictHostPathAuthority; executable content identity belongs to Hashmarks. This
issuer never discovers another PATH, installs a tool or binds the physical toolchain.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import (
    ISSUER_PROTOCOL,
    SIGNATURE_DOMAIN,
    IssuerEvidence,
)
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    canonical_bytes,
    digest,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import required_runtime_tool_names
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.integrations import content_snapshot
from oh_goon.integrations.fleet_plan_repository_scope import admitted_active_plan
from oh_goon.native_issuer.enforcer_lace_captured_run_issuer import _read_original_admission
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
from oh_goon.runtime.journal import Journal

TOOL_FIELD = "native_tool_identity"
TOOL_DOMAIN = "oh-goon/fleet-run-native-execution-tools/v1"


def _admitted_tools(journal: Journal, run_id: str, *, prework: PreworkRunCapture | None = None):
    admitted, revision = _read_original_admission(journal, run_id, prework=prework)
    selected = tuple(item.repository_id for item in admitted.repository_admissions)
    if (
        len(selected) != len(set(selected))
        or selected != tuple(sorted(selected))
        or {target.repository_id for target in admitted.targets} != set(selected)
        or len(admitted.targets) != len(selected)
    ):
        raise TrustProtocolError("durable Run tool scope differs from selected repositories")
    if admitted.target.plan.native_agent is not None:
        raise TrustProtocolError(
            "Native Agent executable authority is unsupported by the host-tool issuer"
        )
    try:
        plans = tuple(
            admitted_active_plan(admitted.target.plan, item)
            for item in admitted.repository_admissions
        )
        names = tuple(
            sorted({tool for plan in plans for tool in required_runtime_tool_names(plan)})
        )
    except (FleetPlanInvalid, TypeError, ValueError) as exc:
        raise TrustProtocolError("durable Run tool selection cannot be proven") from exc
    if not names:
        raise TrustProtocolError("durable Run has no host tool authority to attest")
    return admitted, revision, names


def _trusted_path_authority(value: StrictHostPathAuthority) -> StrictHostPathAuthority:
    if not isinstance(value, StrictHostPathAuthority):
        raise TrustProtocolError("deployment-owned StrictHostPathAuthority required")
    try:
        verified = StrictHostPathAuthority.create(
            trusted_roots=value.trusted_roots,
            tool_search_paths=value.tool_search_paths,
            runtime_inspector=value.runtime_inspector,
        )
    except (FleetPlanInvalid, ValueError, OSError) as exc:
        raise TrustProtocolError("deployment tool authority is no longer admissible") from exc
    if verified != value:
        raise TrustProtocolError("deployment tool authority has noncanonical roots")
    return verified


def _observe(
    names: tuple[str, ...], authority: StrictHostPathAuthority
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for name in names:
        try:
            path = authority.tool(name)
            previous = path.stat()
            identity = content_snapshot.snapshot_identity(path)
            current = path.stat()
        except (FleetPlanInvalid, ImportError, ValueError, OSError) as exc:
            raise TrustProtocolError("native execution tool owner observation unavailable") from exc
        if (
            previous.st_dev,
            previous.st_ino,
            previous.st_mode,
            previous.st_size,
            previous.st_mtime_ns,
        ) != (
            current.st_dev,
            current.st_ino,
            current.st_mode,
            current.st_size,
            current.st_mtime_ns,
        ):
            raise TrustProtocolError("native execution tool changed during owner observation")
        if (
            not path.is_absolute()
            or not path.is_file()
            or path.is_symlink()
            or path.resolve() != path
            or not os.access(path, os.X_OK)
            or not isinstance(identity.sha256, str)
            or len(identity.sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in identity.sha256)
            or identity.byte_count < 0
            or identity.entry_count < 1
        ):
            raise TrustProtocolError("native execution tool identity is not canonical")
        entries.append(
            {
                "name": name,
                "canonical_path": str(path),
                "snapshot_sha256": identity.sha256,
                "byte_count": identity.byte_count,
                "entry_count": identity.entry_count,
                "native_device": previous.st_dev,
                "native_inode": previous.st_ino,
                "native_mode": previous.st_mode,
            }
        )
    return tuple(entries)


def observed_admitted_run_native_tool_identity(
    *,
    journal: Journal,
    run_id: str,
    path_authority: StrictHostPathAuthority,
) -> str:
    """Project existing native tools without signing or authorizing execution."""
    authority = _trusted_path_authority(path_authority)
    admitted, revision, names = _admitted_tools(journal, run_id)
    before = _observe(names, authority)
    after = _observe(names, authority)
    later, next_revision, next_names = _admitted_tools(journal, run_id)
    if before != after or admitted != later or revision != next_revision or names != next_names:
        raise TrustProtocolError("native execution tools or Run changed during observation")
    return digest(TOOL_DOMAIN, canonical_bytes({"tools": list(before)}))


def issue_observed_captured_run_native_tool_identity(
    *,
    journal: Journal,
    contract: AdmittedRunContract,
    path_authority: StrictHostPathAuthority,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
    prework: PreworkRunCapture | None = None,
) -> IssuerEvidence:
    """Sign a complete tool identity only after two native owner observations."""
    contract.verify()
    if (
        not isinstance(signing_key, Ed25519PrivateKey)
        or not isinstance(issuer_id, str)
        or not issuer_id
        or not issuer_id.isascii()
        or any(c in issuer_id for c in "\x00\n/")
    ):
        raise TrustProtocolError("independent native tool signing authority required")
    authority = _trusted_path_authority(path_authority)
    fields = contract.fields
    admitted, revision, names = _admitted_tools(journal, fields["run_id"], prework=prework)
    before = _observe(names, authority)
    identity = digest(TOOL_DOMAIN, canonical_bytes({"tools": list(before)}))
    if fields[TOOL_FIELD] != identity:
        raise TrustProtocolError("contract native tool identity differs from selected authority")
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": TOOL_FIELD,
            "run_id": fields["run_id"],
            "contract_identity": contract.identity,
            "identity": identity,
            "executor_generation": fields["executor_generation"],
        }
    )
    after = _observe(names, authority)
    later, next_revision, next_names = _admitted_tools(journal, fields["run_id"], prework=prework)
    if before != after or admitted != later or revision != next_revision or names != next_names:
        raise TrustProtocolError("native execution tools or Run changed during issuance")
    message = SIGNATURE_DOMAIN + TOOL_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(TOOL_FIELD, issuer_id, payload, signing_key.sign(message).hex())
