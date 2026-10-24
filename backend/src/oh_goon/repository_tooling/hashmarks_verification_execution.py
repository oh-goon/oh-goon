from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TypeVar

from oh_goon.integrations.hashmarks.verification_selection import (
    VerifiedHashmarksVerificationSelection,
)
from oh_goon.repository_tooling._hashmarks_verification_execution_primitives import (
    build_execution_coverage,
)
from oh_goon.runtime.execution.pytest_projection import project_pytest_argv
from oh_goon.runtime.execution.shard_models import (
    ShardExecutionPlan,
    ShardExecutionReport,
    ShardWork,
)

_Member = TypeVar("_Member")

_RECEIPT_SCHEMA = "oh-goon.hashmarks-verification-execution-receipt.v4"
_GROUPING_POLICY_SCHEMA = "oh-goon.verification-execution-grouping-policy.v1"


@dataclass(frozen=True, slots=True)
class VerificationExecutionGroupingPolicy:
    """Oh-Goon-owned physical grouping policy over already-admitted membership."""

    max_members_per_group: int

    def __post_init__(self) -> None:
        if self.max_members_per_group < 1:
            raise ValueError("max_members_per_group must be >= 1")

    @property
    def policy_identity(self) -> str:
        return _identity(
            {
                "schema": _GROUPING_POLICY_SCHEMA,
                "authority": "oh-goon",
                "ordering": "admitted-selection-order",
                "max_members_per_group": self.max_members_per_group,
            }
        )

    def group(self, members: tuple[_Member, ...]) -> tuple[tuple[_Member, ...], ...]:
        """Partition in admitted order without selecting, dropping, duplicating, or reordering."""

        if not members:
            raise ValueError("verification grouping requires nonempty admitted membership")
        groups = tuple(
            members[index : index + self.max_members_per_group]
            for index in range(0, len(members), self.max_members_per_group)
        )
        if tuple(member for group in groups for member in group) != members:
            raise AssertionError("physical grouping must conserve admitted membership exactly")
        return groups


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def build_verification_execution_plan(
    selection: VerifiedHashmarksVerificationSelection,
    *,
    command_prefix: tuple[str, ...],
    grouping_policy: VerificationExecutionGroupingPolicy,
) -> ShardExecutionPlan:
    """Freeze Hashmarks membership, then apply explicit Oh-Goon-owned physical grouping."""

    if not command_prefix:
        raise ValueError("verification execution requires a nonempty command prefix")
    structured_groups = grouping_policy.group(selection.members)
    groups = tuple(
        tuple(member.executable_selector for member in group) for group in structured_groups
    )
    shards = tuple(
        ShardWork(
            shard_id=f"hashmarks-verification-{index}",
            argv=project_pytest_argv(command_prefix, structured_group),
            work_identity=_identity(
                {
                    "schema": "oh-goon.hashmarks-verification-work/v3",
                    "selected_membership_identity": selection.membership_identity,
                    "batch_ordinal": index,
                    "members": list(group),
                    "pytest_projection_identities": [
                        member.projection_identity for member in structured_group
                    ],
                    "command_prefix": list(command_prefix),
                }
            ),
            members=group,
            command_prefix=command_prefix,
            pytest_members=structured_group,
            original_admitted_member_identities=tuple(
                member.member_id for member in structured_group
            ),
        )
        for index, (group, structured_group) in enumerate(
            zip(groups, structured_groups, strict=True)
        )
    )
    return ShardExecutionPlan(
        plan_identity=_identity(
            {
                "schema": "oh-goon.hashmarks-verification-plan/v3",
                "selected_membership_identity": selection.membership_identity,
                "grouping_policy_identity": grouping_policy.policy_identity,
                "command_prefix": list(command_prefix),
                "groups": [list(group) for group in groups],
                "pytest_projection_identities": [
                    member.projection_identity for member in selection.members
                ],
            }
        ),
        generation_identity=_identity(
            {
                "repository_identity": selection.repository_identity,
                "source_identity": selection.source_identity,
            }
        ),
        shards=shards,
        source_schema="hashmarks.downstream-verification-contract.v2",
        producer_version=selection.producer_version,
        repository_evidence_identity=selection.repository_provenance_identity,
        repository_content_identity=selection.repository_identity,
        producer_artifact_identity=selection.producer_artifact_identity,
        producer_implementation_identity=selection.producer_implementation_identity,
        producer_contract_identity=selection.downstream_contract_identity,
        verification_selection_evidence_identity=selection.repository_provenance_identity,
        selected_membership_identity=selection.membership_identity,
        verification_revision_identity=selection.source_identity,
        producer_repository_generation=selection.codemap_generation,
        producer_identity_generation=selection.identity_generation,
        producer_evidence_hashes=selection.evidence_hashes,
        selected_test_nodes=selection.selected_test_nodes,
        unproven_owner_members=selection.unproven_owner_members,
    )


@dataclass(frozen=True, slots=True)
class HashmarksVerificationExecutionReceipt:
    schema: str
    repository_identity: str
    source_identity: str
    hashmarks_identity: str
    producer_artifact_identity: str
    producer_implementation_identity: str
    repository_provenance_identity: str
    selected_membership_identity: str
    selected: tuple[str, ...]
    selected_projection_identities: tuple[str, ...]
    attempted: tuple[str, ...]
    resumed: tuple[str, ...]
    completed: tuple[str, ...]
    missing: tuple[str, ...]
    duplicates: tuple[str, ...]
    extras: tuple[str, ...]
    retry_bisection_lineage: tuple[dict[str, object], ...]
    terminal_membership_identity: str | None
    terminal_projection_identities: tuple[str, ...]
    terminal_success: bool
    receipt_identity: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def build_verification_execution_receipt(
    selection: VerifiedHashmarksVerificationSelection,
    report: ShardExecutionReport,
) -> HashmarksVerificationExecutionReceipt:
    """Report terminal coverage against the original frozen Hashmarks selection."""

    expected = (
        (report.selected_membership_identity, selection.membership_identity, "membership identity"),
        (
            report.verification_selection_evidence_identity,
            selection.repository_provenance_identity,
            "provenance",
        ),
        (
            report.producer_artifact_identity,
            selection.producer_artifact_identity,
            "Hashmarks artifact identity",
        ),
        (
            report.producer_implementation_identity,
            selection.producer_implementation_identity,
            "Hashmarks producer implementation identity",
        ),
    )
    for observed, admitted, label in expected:
        if observed != admitted:
            raise ValueError(
                f"execution report {label} does not match admitted Hashmarks selection"
            )

    coverage = build_execution_coverage(selection.selected_test_nodes, report)
    terminal_identity = selection.membership_identity if coverage.terminal_success else None
    payload = {
        "schema": _RECEIPT_SCHEMA,
        "repository_identity": selection.repository_identity,
        "source_identity": selection.source_identity,
        "hashmarks_identity": f"hashmarks:{selection.producer_version}",
        "producer_artifact_identity": selection.producer_artifact_identity,
        "producer_implementation_identity": selection.producer_implementation_identity,
        "repository_provenance_identity": selection.repository_provenance_identity,
        "selected_membership_identity": selection.membership_identity,
        "selected": list(selection.selected_test_nodes),
        "selected_projection_identities": [
            member.projection_identity for member in selection.members
        ],
        "attempted": list(coverage.attempted),
        "resumed": list(coverage.resumed),
        "completed": list(coverage.completed),
        "missing": list(coverage.missing),
        "duplicates": list(coverage.duplicates),
        "extras": list(coverage.extras),
        "retry_bisection_lineage": list(coverage.lineage),
        "terminal_membership_identity": terminal_identity,
        "terminal_projection_identities": [
            member.projection_identity for member in selection.members
        ]
        if coverage.terminal_success
        else [],
        "terminal_success": coverage.terminal_success,
    }
    return HashmarksVerificationExecutionReceipt(
        schema=_RECEIPT_SCHEMA,
        repository_identity=selection.repository_identity,
        source_identity=selection.source_identity,
        hashmarks_identity=f"hashmarks:{selection.producer_version}",
        producer_artifact_identity=selection.producer_artifact_identity,
        producer_implementation_identity=selection.producer_implementation_identity,
        repository_provenance_identity=selection.repository_provenance_identity,
        selected_membership_identity=selection.membership_identity,
        selected=selection.selected_test_nodes,
        selected_projection_identities=tuple(
            member.projection_identity for member in selection.members
        ),
        attempted=coverage.attempted,
        resumed=coverage.resumed,
        completed=coverage.completed,
        missing=coverage.missing,
        duplicates=coverage.duplicates,
        extras=coverage.extras,
        retry_bisection_lineage=coverage.lineage,
        terminal_membership_identity=terminal_identity,
        terminal_projection_identities=tuple(
            member.projection_identity for member in selection.members
        )
        if coverage.terminal_success
        else (),
        terminal_success=coverage.terminal_success,
        receipt_identity=_identity(payload),
    )
