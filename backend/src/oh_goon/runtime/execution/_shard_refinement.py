from __future__ import annotations

import hashlib
import json

from oh_goon.core.models.pytest_selection import StructuredPytestMember
from oh_goon.runtime.execution.pytest_projection import project_pytest_argv
from oh_goon.runtime.execution.shard_models import ShardWork


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _work_identity(
    parent: ShardWork,
    members: tuple[str, ...],
    pytest_members: tuple[StructuredPytestMember, ...],
    ordinal: int,
) -> str:
    return _identity(
        {
            "schema": "oh-goon.refined-shard-work/v2",
            "described_work_identity": parent.described_work_identity,
            "parent_work_identity": parent.work_identity,
            "refinement_depth": parent.refinement_depth + 1,
            "ordinal": ordinal,
            "command_prefix": list(parent.command_prefix),
            "members": list(members),
            "pytest_projection_identities": [item.projection_identity for item in pytest_members],
            "original_admitted_member_identities": [item.member_id for item in pytest_members],
            "isolated_process": parent.isolated_process,
        }
    )


def refine_children(parent: ShardWork) -> tuple[ShardWork, ShardWork]:
    if len(parent.members) < 2:
        raise ValueError("timeout refinement requires at least two ordered members")
    midpoint = len(parent.members) // 2
    member_groups = (parent.members[:midpoint], parent.members[midpoint:])
    pytest_groups = (
        (
            parent.pytest_members[:midpoint],
            parent.pytest_members[midpoint:],
        )
        if parent.pytest_members
        else ((), ())
    )
    children: list[ShardWork] = []
    for ordinal, (members, pytest_members) in enumerate(
        zip(member_groups, pytest_groups, strict=True)
    ):
        argv = (
            project_pytest_argv(parent.command_prefix, pytest_members)
            if pytest_members
            else (*parent.command_prefix, *members)
        )
        children.append(
            ShardWork(
                shard_id=f"{parent.shard_id}.r{parent.refinement_depth + 1}.{ordinal}",
                argv=argv,
                work_identity=_work_identity(parent, members, pytest_members, ordinal),
                isolated_process=parent.isolated_process,
                described_work_identity=parent.described_work_identity,
                members=members,
                command_prefix=parent.command_prefix,
                parent_work_identity=parent.work_identity,
                refinement_depth=parent.refinement_depth + 1,
                pytest_members=pytest_members,
                original_admitted_member_identities=tuple(
                    member.member_id for member in pytest_members
                ),
            )
        )
    return children[0], children[1]
