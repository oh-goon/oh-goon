"""M159: non-live native uv lock issuer for exact durable Fleet Plan requirements.

The admitted Plan chooses the projects. Native uv owns lock freshness; the only
OH-Goon projection here is immutable file-byte identity and selected scope.
This is not production pre-work admission or immutable filesystem pinning.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

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
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_requirements import UvLockMode, UvProjectRequirement
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.integrations.native_uv_lock import native_uv_locked_project_issue
from oh_goon.native_issuer.enforcer_lace_captured_run_issuer import _read_original_admission
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
from oh_goon.runtime.journal import Journal

LOCK_FIELD = "lock_identity"
LOCK_DOMAIN = "oh-goon/fleet-run-selected-native-uv-locks/v2"


def _selected_projects(
    journal: Journal, run_id: str, *, prework: PreworkRunCapture | None = None
) -> tuple[RunAdmittedEvent, int, dict[tuple[str, UvLockMode], tuple[str, ...]], dict[str, Path]]:
    admitted, revision = _read_original_admission(journal, run_id, prework=prework)
    target = admitted.target
    if not isinstance(target, FleetPlanTarget):
        raise TrustProtocolError("native uv lock selection requires a Fleet Plan target")
    requirements = tuple(r for r in target.plan.requirements if isinstance(r, UvProjectRequirement))
    if not requirements:
        raise TrustProtocolError("admitted Plan has no selected native uv lock authority")
    if (
        isinstance(target.plan.execution, CicdExecutionPolicy)
        and not target.plan.execution.trust_repository_code
        and any(requirement.lock_mode == "locked" for requirement in requirements)
    ):
        raise TrustProtocolError(
            "Untrusted locked repository UV requires isolated native freshness before "
            "pre-admission proof; host metadata checks are unsupported"
        )
    pairs = tuple(
        sorted(
            ((r.project, r.group, r.lock_mode) for r in requirements),
            key=lambda pair: (pair[0], pair[1] or "", pair[2]),
        )
    )
    if len(pairs) != len(set(pairs)):
        raise TrustProtocolError("duplicate uv project requirement")
    groups = {
        (project, mode): tuple(
            sorted(g for p, g, m in pairs if p == project and m == mode and g is not None)
        )
        for project, mode in sorted({(p, m) for p, _, m in pairs})
    }
    roots = {target.repository_id: target.path for target in admitted.targets}
    selected = tuple(item.repository_id for item in admitted.repository_admissions)
    if len(roots) != len(admitted.targets) or set(roots) != set(selected):
        raise TrustProtocolError("durable Run repository scope differs from native lock selection")
    return admitted, revision, groups, roots


def _safe_file_identity(path: Path) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
            raise TrustProtocolError(
                "native lock authority file must be unique regular non-symlink"
            )
        with path.open("rb") as stream:
            data = stream.read()
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError as exc:
        raise TrustProtocolError("native lock authority file unreadable") from exc
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ):
        raise TrustProtocolError("native lock authority bytes changed during observation")
    return hashlib.sha256(data).hexdigest()


def _measure(
    groups: dict[tuple[str, UvLockMode], tuple[str, ...]],
    roots: dict[str, Path],
    *,
    native_check: bool,
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for repository_id in sorted(roots):
        root = roots[repository_id]
        if (
            not root.is_absolute()
            or root.is_symlink()
            or not root.is_dir()
            or root.resolve() != root
        ):
            raise TrustProtocolError("unsafe durable repository root for native lock")
        space = RepositoryPathSpace.from_root(root)
        for (relative, lock_mode), selected_groups in groups.items():
            try:
                project = space.resolve(
                    relative,
                    label="admitted locked uv project",
                    kind="directory",
                    require_exists=True,
                )
            except (ValueError, OSError) as exc:
                raise TrustProtocolError("admitted locked uv project is not contained") from exc
            if project.resolve() != root / relative or project.is_symlink():
                raise TrustProtocolError("uv project traverses a filesystem alias")
            intent = _safe_file_identity(project / "pyproject.toml")
            lock = _safe_file_identity(project / "uv.lock")
            if native_check and lock_mode == "locked":
                issue = native_uv_locked_project_issue(project)
                if issue is not None:
                    raise TrustProtocolError("native uv rejected admitted lock freshness: " + issue)
            entries.append(
                {
                    "repository_id": repository_id,
                    "project": relative,
                    "groups": list(selected_groups),
                    "lock_mode": lock_mode,
                    "pyproject_sha256": intent,
                    "uv_lock_sha256": lock,
                }
            )
    return tuple(entries)


def observed_admitted_run_lock_identity(*, journal: Journal, run_id: str) -> str:
    """Bind selected bytes/mode; check native freshness only when locked is selected.

    This non-live observation cannot replace a pre-work capture/commit transaction.
    """
    admitted, revision, groups, roots = _selected_projects(journal, run_id)
    before = _measure(groups, roots, native_check=True)
    after = _measure(groups, roots, native_check=False)
    later, later_revision = _read_original_admission(journal, run_id, prework=None)
    if before != after or later != admitted or later_revision != revision:
        raise TrustProtocolError("native lock or durable Run authority changed during observation")
    return digest(LOCK_DOMAIN, canonical_bytes({"locks": list(before)}))


def issue_observed_captured_run_lock_identity(
    *,
    journal: Journal,
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
    prework: PreworkRunCapture | None = None,
) -> IssuerEvidence:
    """Sign selected native lock bytes and mode without adding frozen freshness checks."""
    contract.verify()
    if (
        not isinstance(signing_key, Ed25519PrivateKey)
        or not isinstance(issuer_id, str)
        or not issuer_id.isascii()
        or not issuer_id
        or any(c in issuer_id for c in "\x00\n/")
    ):
        raise TrustProtocolError("independent native lock signing authority required")
    values = contract.fields
    admitted, revision, groups, roots = _selected_projects(
        journal, values["run_id"], prework=prework
    )
    before = _measure(groups, roots, native_check=True)
    identity = digest(LOCK_DOMAIN, canonical_bytes({"locks": list(before)}))
    if values[LOCK_FIELD] != identity:
        raise TrustProtocolError("contract lock identity differs from native selected authority")
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": LOCK_FIELD,
            "run_id": values["run_id"],
            "contract_identity": contract.identity,
            "identity": identity,
            "executor_generation": values["executor_generation"],
        }
    )
    after = _measure(groups, roots, native_check=False)
    later, later_revision = _read_original_admission(journal, values["run_id"], prework=prework)
    if before != after or later != admitted or later_revision != revision:
        raise TrustProtocolError("native lock or durable Run authority changed during issuance")
    signature = SIGNATURE_DOMAIN + LOCK_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(LOCK_FIELD, issuer_id, payload, signing_key.sign(signature).hex())
