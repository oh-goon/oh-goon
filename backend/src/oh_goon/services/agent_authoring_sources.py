"""Source acquisition and Fleet reservation through the original Agent owners.

The resolver captures the authorized trees once and returns the passive core
source contract. The original Fleet transaction consumes that exact delivery;
neither source capture nor its journal bridge launches an Agent Step.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models import agent_source
from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects
from oh_goon.core.models.fleet_plan_composition import (
    PlanInputDefinition,
    PlanInputType,
    PlanInputValue,
    validate_domain_input_value,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.core.source_tree_policy import is_transient_source_path

if TYPE_CHECKING:
    from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
    from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustGrant


def capture_agent_sources(
    definitions: tuple[PlanInputDefinition, ...],
    values: dict[str, PlanInputValue],
    *,
    repository_root: Path,
    skill_root: Path,
    allowed_acceptance: tuple[str, ...],
    plan_source_sha256: str,
    plan_graph_identity: str,
    skill_aliases: Mapping[str, str] | None = None,
) -> agent_source.FrozenAgentSourceCapture:
    """Capture bytes ONCE via the existing AG-01 reader, without effects.

    Not exposed as a CLI writer or agent runner. A future Step must carry these
    bytes through original admission and must not re-open source paths.
    """
    captured: dict[str, agent_source.FrozenSourceTree] = {}
    preview = explain_agent_sources(
        definitions,
        values,
        repository_root=repository_root,
        skill_root=skill_root,
        allowed_acceptance=allowed_acceptance,
        plan_source_sha256=plan_source_sha256,
        skill_aliases=skill_aliases,
        _captured=captured,
    )
    frozen = agent_source.FrozenAgentSourceCapture(
        plan_source_sha256=plan_source_sha256,
        plan_graph_identity=plan_graph_identity,
        acceptance_profile=str(preview["acceptance"]["profile"]),
        repository_reference=str(preview["repository"]["reference"]),
        resolved_skill_reference=str(preview["skill"]["reference"]),
        authored_skill_reference=str(preview["authored_skill_reference"]),
        repository=captured["repository"],
        skill=captured["skill"],
    )
    frozen.verify()
    return frozen


def explain_agent_sources(
    definitions: tuple[PlanInputDefinition, ...],
    values: dict[str, PlanInputValue],
    *,
    repository_root: Path,
    skill_root: Path,
    allowed_acceptance: tuple[str, ...],
    plan_source_sha256: str,
    skill_aliases: Mapping[str, str] | None = None,
    _captured: dict[str, agent_source.FrozenSourceTree] | None = None,
) -> dict[str, object]:
    """Project immutable read-only identities without admitting or executing work."""
    declared: dict[PlanInputType, str] = {}
    for definition in definitions:
        if definition.input_type not in {
            PlanInputType.REPOSITORY_REFERENCE,
            PlanInputType.SKILL_REFERENCE,
            PlanInputType.ACCEPTANCE_PROFILE,
        }:
            continue
        if definition.input_type in declared:
            raise FleetPlanInvalid(f"AG-01 has duplicate {definition.input_type.value} inputs")
        value = values.get(definition.name)
        if not isinstance(value, str) or not value:
            raise FleetPlanInvalid(f"AG-01 requires {definition.input_type.value}")
        declared[definition.input_type] = value
    if set(declared) != {
        PlanInputType.REPOSITORY_REFERENCE,
        PlanInputType.SKILL_REFERENCE,
        PlanInputType.ACCEPTANCE_PROFILE,
    }:
        raise FleetPlanInvalid(
            "AG-01 requires repository_reference, skill_reference and acceptance_profile"
        )

    accepted = declared[PlanInputType.ACCEPTANCE_PROFILE]
    if (
        not allowed_acceptance
        or accepted not in allowed_acceptance
        or len(set(allowed_acceptance)) != len(allowed_acceptance)
    ):
        raise FleetPlanInvalid("AG-01 acceptance profile is not explicitly authorized")
    repo_ref = declared[PlanInputType.REPOSITORY_REFERENCE]
    authored_skill_ref = declared[PlanInputType.SKILL_REFERENCE]
    skill_ref = authored_skill_ref
    git_pin = None
    if not skill_ref.startswith(("local:", "github:")):
        # No environment lookup, mutable team alias directory, or second
        # resolver. The caller supplies the exact explicit prework mapping.
        if skill_aliases is None or skill_ref not in skill_aliases:
            raise FleetPlanInvalid("AG-01 skill alias has no explicit frozen expansion")
        from oh_goon.services.agent_skill_registry import FrozenSkillRegistry

        if isinstance(skill_aliases, FrozenSkillRegistry):
            git_pin = skill_aliases.pins.get(skill_ref)
        skill_ref = skill_aliases[skill_ref]
        # A Gitlink can only come from the original adjacent .goonskills
        # registry, never an arbitrary explicit --skill-alias expansion.
        if git_pin is not None:
            if skill_ref != git_pin.reference:
                raise FleetPlanInvalid("SKILL-2 pinned registry reference changed")
            git_pin.verify(skill_root)
            skill_ref = "local:" + git_pin.path
        try:
            validate_domain_input_value(
                PlanInputDefinition(name="skill", input_type=PlanInputType.SKILL_REFERENCE),
                skill_ref,
                label="alias expansion",
            )
        except ValueError as exc:
            raise FleetPlanInvalid(
                "AG-01 skill alias expands to an invalid source reference"
            ) from exc
        if not skill_ref.startswith(("local:", "github:")):
            raise FleetPlanInvalid("AG-01 chained aliases are not allowed")
    if not repo_ref.startswith("local:"):
        raise FleetPlanInvalid(
            "AG-01 repository acquisition requires an explicitly authorized local root"
        )
    if skill_ref.startswith("github:"):
        # A full SHA alone cannot authenticate *where* content was acquired.
        # Only original source-acquisition authority may verify/publish a
        # pinned global checkout. Do not invent a parallel network fetcher.
        raise FleetPlanInvalid(
            "AG-01 pinned GitHub skill requires original source-acquisition proof; unavailable"
        )
    if not skill_ref.startswith("local:"):
        raise FleetPlanInvalid("AG-01 skill must use a bounded, immutable source reference")

    repository = _snapshot_authorized_tree(
        repository_root,
        repo_ref,
        label="repository",
        max_bytes=agent_source.MAX_REPOSITORY_BYTES,
        captured=_captured,
    )
    # For a Gitlink, retain the *same* single AG-01 fd-anchored byte read.
    # Compare those already-captured bytes to the committed Git blob identities;
    # Git metadata is not a second reader or materialization authority.
    skill_capture = _captured if _captured is not None else {}
    skill = _snapshot_authorized_tree(
        skill_root,
        skill_ref,
        label="skill",
        max_bytes=agent_source.MAX_SKILL_BYTES,
        captured=skill_capture if git_pin is not None else _captured,
    )
    if git_pin is not None:
        git_pin.verify(skill_root, skill_capture["skill"])
        skill["reference"] = git_pin.reference
        skill["source_origin"] = "COMMITTED_GITLINK_OFFLINE_CAPTURE"
    if "SKILL.md" not in {item["path"] for item in skill["members"]}:
        raise FleetPlanInvalid("AG-01 skill package must include a regular SKILL.md at its root")
    return {
        "schema": "oh-goon.agent-authoring-source-preview/v1",
        "authority": "READ_ONLY_PREWORK_NOT_ADMITTED",
        "plan_source_sha256": plan_source_sha256,
        "repository": repository,
        "skill": skill,
        "authored_skill_reference": authored_skill_ref,
        "acceptance": {"profile": accepted, "authorization": "EXPLICIT_CALLER_ALLOWLIST"},
        "capability_authority": "NONE",
        "runtime_provider": "UNRESOLVED_AG02",
        "egress_recipients": [],
        "original_enforcer_admission": "NOT_ATTEMPTED",
        "native_containment": "NOT_QUALIFIED",
        "transported_immutable_snapshot": False,
    }


def _snapshot_authorized_tree(
    root: Path,
    reference: str,
    *,
    label: str,
    max_bytes: int,
    captured: dict[str, agent_source.FrozenSourceTree] | None = None,
) -> dict[str, Any]:
    """Read exactly once from an fd-anchored authorized tree.

    Every child open is relative to its already verified parent directory fd.
    Pathname re-resolution or symlink swaps cannot redirect reads outside the
    chosen authorized root. Descriptor identities are checked across traversal.
    """
    if root.is_symlink() or not root.is_dir():
        raise FleetPlanInvalid(f"AG-01 {label} authorized root must be a regular directory")
    relative = PurePosixPath(reference.removeprefix("local:"))
    if not relative.parts or any(
        part in {"", ".", ".."} or part.startswith(".") for part in relative.parts
    ):
        raise FleetPlanInvalid(f"AG-01 {label} path contains a forbidden component")

    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise FleetPlanInvalid(f"AG-02 {label} requires native fd-anchored source acquisition")
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    opened_dirs: list[int] = []
    opened_states: list[os.stat_result] = []
    members: list[dict[str, object]] = []
    frozen_members: list[agent_source.FrozenSourceMember] = []
    total = 0
    directory_count = 0

    def identity(item: os.stat_result) -> tuple[int, ...]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    def read_dir(dir_fd: int, subpath: Path, depth: int, root_device: int) -> None:
        nonlocal total, directory_count
        directory_count += 1
        if directory_count > agent_source.MAX_SOURCE_ENTRIES or depth > 64:
            raise FleetPlanInvalid(f"AG-01 {label} closure exceeds directory budget")
        before_dir = os.fstat(dir_fd)
        if before_dir.st_dev != root_device:
            raise FleetPlanInvalid(f"AG-01 {label} crosses a foreign filesystem")
        with os.scandir(dir_fd) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            rel = subpath / entry.name
            if is_transient_source_path(rel):
                continue
            before = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                next_fd = os.open(entry.name, dir_flags, dir_fd=dir_fd)
                try:
                    if identity(os.fstat(next_fd)) != identity(before):
                        raise FleetPlanInvalid(f"AG-01 {label} directory changed: {rel.as_posix()}")
                    read_dir(next_fd, rel, depth + 1, root_device)
                    after = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
                    if identity(after) != identity(before):
                        raise FleetPlanInvalid(f"AG-01 {label} directory changed: {rel.as_posix()}")
                finally:
                    os.close(next_fd)
                continue
            if len(members) >= agent_source.MAX_SOURCE_ENTRIES:
                raise FleetPlanInvalid(f"AG-01 {label} closure exceeds entry budget")
            if before.st_nlink != 1:
                raise FleetPlanInvalid(f"AG-01 {label} contains hardlink: {rel.as_posix()}")
            if not stat.S_ISREG(before.st_mode):
                raise FleetPlanInvalid(
                    f"AG-01 {label} contains non-regular source: {rel.as_posix()}"
                )
            if before.st_dev != root_device:
                raise FleetPlanInvalid(
                    f"AG-01 {label} contains foreign filesystem file: {rel.as_posix()}"
                )
            if (
                before.st_size > agent_source.MAX_SOURCE_FILE_BYTES
                or total + before.st_size > max_bytes
            ):
                raise FleetPlanInvalid(f"AG-01 {label} exceeds source byte budget")
            fd = os.open(entry.name, file_flags, dir_fd=dir_fd)
            try:
                if identity(os.fstat(fd)) != identity(before):
                    raise FleetPlanInvalid(
                        f"AG-01 {label} source changed before read: {rel.as_posix()}"
                    )
                digest = hashlib.sha256()
                chunks: list[bytes] | None = [] if captured is not None else None
                count = 0
                while chunk := os.read(
                    fd, min(1024 * 1024, agent_source.MAX_SOURCE_FILE_BYTES + 1 - count)
                ):
                    count += len(chunk)
                    if count > agent_source.MAX_SOURCE_FILE_BYTES or total + count > max_bytes:
                        raise FleetPlanInvalid(f"AG-01 {label} source grew beyond budget")
                    digest.update(chunk)
                    if chunks is not None:
                        chunks.append(chunk)
                after_fd = os.fstat(fd)
            finally:
                os.close(fd)
            after_path = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
            if (
                identity(before) != identity(after_fd)
                or identity(after_fd) != identity(after_path)
                or count != before.st_size
            ):
                raise FleetPlanInvalid(
                    f"AG-01 {label} source changed during inspection: {rel.as_posix()}"
                )
            total += count
            members.append({"path": rel.as_posix(), "sha256": digest.hexdigest(), "bytes": count})
            if chunks is not None:
                frozen_members.append(
                    agent_source.FrozenSourceMember(
                        path=rel.as_posix(),
                        sha256=digest.hexdigest(),
                        data=b"".join(chunks),
                    )
                )
        if identity(before_dir) != identity(os.fstat(dir_fd)):
            raise FleetPlanInvalid(f"AG-01 {label} directory changed during inspection")

    try:
        root_before = root.lstat()
        root_fd = os.open(root, dir_flags)
        opened_dirs.append(root_fd)
        opened_states.append(root_before)
        if identity(root_before) != identity(os.fstat(root_fd)):
            raise FleetPlanInvalid(f"AG-01 {label} authorized root changed")
        current = root_fd
        for part in relative.parts:
            next_before = os.stat(part, dir_fd=current, follow_symlinks=False)
            next_fd = os.open(part, dir_flags, dir_fd=current)
            opened_dirs.append(next_fd)
            opened_states.append(next_before)
            if identity(next_before) != identity(os.fstat(next_fd)):
                raise FleetPlanInvalid(f"AG-01 {label} selection changed")
            current = next_fd
        read_dir(current, Path("."), 0, root_before.st_dev)
        for index, fd in enumerate(opened_dirs):
            ancestor_now = (
                root.lstat()
                if index == 0
                else os.stat(
                    relative.parts[index - 1], dir_fd=opened_dirs[index - 1], follow_symlinks=False
                )
            )
            if identity(os.fstat(fd)) != identity(opened_states[index]) or identity(
                ancestor_now
            ) != identity(opened_states[index]):
                raise FleetPlanInvalid(f"AG-01 {label} source ancestry changed")
        # The original path still names the opened root, not a replacement.
        if identity(root.lstat()) != identity(os.fstat(root_fd)):
            raise FleetPlanInvalid(f"AG-01 {label} authorized root changed")
    except (OSError, NotADirectoryError) as exc:
        raise FleetPlanInvalid(f"AG-01 {label} invalid or changed source directory") from exc
    finally:
        for fd in reversed(opened_dirs):
            os.close(fd)

    members.sort(key=lambda member: str(member["path"]))
    if not members:
        raise FleetPlanInvalid(f"AG-01 {label} source closure is empty")
    content_id = stable_id("agent-source-closure-v1", {"members": members})
    if captured is not None:
        frozen = agent_source.FrozenSourceTree(
            kind=label,
            content_identity=content_id,
            members=tuple(sorted(frozen_members, key=lambda item: item.path)),
        )
        frozen.verify()
        captured[label] = frozen
    return {
        "reference": reference,
        "content_identity": content_id,
        "members": members,
        "entry_count": len(members),
        "byte_count": total,
        "source_origin": "AUTHORIZED_LOCAL_ROOT",
        "snapshot_custody": "READ_ONCE_PREVIEW_ONLY",
    }


def reserve_verified_agent_source_in(
    journal: "CausalRunJournal",
    run_id: str,
    *,
    epoch: int,
    lease_identity: str,
    auth: "ReceiptAuthenticator",
    signer_grant: "SignerTrustGrant",
    step_id: str,
    attempt_identity: str,
    executor_generation: int,
    packet: bytes,
    expected_handoff_identity: str,
    after_reservation: Callable[[agent_source.FrozenAgentStepHandoff, int], None] | None = None,
) -> int:
    """Single AG-02B2A canonical decoder -> original Fleet journal write cut.

    This non-executable bridge owns no transaction, signer, scheduler or new
    journal. It must be called by the original signed admitted Fleet owner.
    Neither this prework method nor its receipt can launch an Agent Step.
    """
    from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal

    if type(journal) is not CausalRunJournal or not journal.connection.in_transaction:
        raise CausalJournalError("original Fleet journal write transaction required")
    handoff = agent_source.FrozenAgentStepHandoff.decode_prework_delivery(
        packet,
        expected_handoff_identity=expected_handoff_identity,
    )
    if handoff.step_id != step_id:
        raise CausalJournalError("agent delivery Step differs from admitted Step")
    reservation_seq = journal.reserve_agent_source_prework_in(
        run_id,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        signer_grant=signer_grant,
        step_id=step_id,
        attempt_identity=attempt_identity,
        executor_generation=executor_generation,
        packet=packet,
        expected_handoff_identity=expected_handoff_identity,
        # SKILL-3C: only the original, fully verified AG-02 source is allowed
        # to supply an effect demand to the original Enforcer journal cut.
        # This is NOT a grant, even when the Run envelope agrees with it.
        requested_effects=(
            handoff.requested_effects.model_dump(mode="json")
            if handoff.requested_effects != AgentStepRequestedEffects()
            else None
        ),
    )
    if after_reservation is not None:
        after_reservation(handoff, reservation_seq)
    return reservation_seq
