from __future__ import annotations

import json
import os
from pathlib import Path

from oh_goon.runtime.execution._shard_refinement import refine_children
from oh_goon.runtime.execution.shard_models import (
    PASS_EVIDENCE_SCHEMA,
    REFINEMENT_EVIDENCE_SCHEMA,
    ShardExecutionContext,
    ShardExecutionPlan,
    ShardWork,
    _identity,
)


def _resume_payload(
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    shard: ShardWork,
) -> dict[str, object]:
    return {
        "schema": PASS_EVIDENCE_SCHEMA,
        "execution_contract_version": context.execution_contract_version,
        "execution_context_identity": context.context_identity,
        "execution_policy_identity": execution_policy_identity,
        "runner_toolchain_identity": context.runner_toolchain_identity,
        "dependency_identity": context.dependency_identity,
        "environment_identity": context.environment_identity,
        "generation_identity": plan.generation_identity,
        "plan_identity": plan.plan_identity,
        "repository_evidence_identity": plan.repository_evidence_identity,
        "repository_content_identity": plan.repository_content_identity,
        "selection_input_identity": plan.selection_input_identity,
        "producer_artifact_identity": plan.producer_artifact_identity,
        "producer_implementation_identity": plan.producer_implementation_identity,
        "producer_algorithm": plan.producer_algorithm,
        "producer_contract_identity": plan.producer_contract_identity,
        "verification_selection_evidence_identity": plan.verification_selection_evidence_identity,
        "selected_membership_identity": plan.selected_membership_identity,
        "verification_revision_identity": plan.verification_revision_identity,
        "selected_test_nodes": list(plan.selected_test_nodes),
        "unproven_owner_members": list(plan.unproven_owner_members),
        "structured_pytest_members": [member.evidence_payload() for member in shard.pytest_members],
        "producer_version": plan.producer_version,
        "source_schema": plan.source_schema,
        "shard_id": shard.shard_id,
        "described_work_identity": shard.described_work_identity,
        "physical_work_identity": shard.work_identity,
        "parent_work_identity": shard.parent_work_identity,
        "refinement_depth": shard.refinement_depth,
        "isolated_process": shard.isolated_process,
        "status": "PASS",
    }


def _state_path(
    state_root: Path,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    shard: ShardWork,
) -> Path:
    payload = _resume_payload(
        plan=plan, context=context, execution_policy_identity=execution_policy_identity, shard=shard
    )
    payload.pop("status")
    key = _identity(payload).removeprefix("sha256:")
    return state_root / "pass" / key[:2] / f"{key}.json"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _persist_pass(
    state_root: Path,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    shard: ShardWork,
) -> None:
    payload = _resume_payload(
        plan=plan, context=context, execution_policy_identity=execution_policy_identity, shard=shard
    )
    payload["evidence_identity"] = _identity(payload)
    _atomic_json(
        _state_path(
            state_root,
            plan=plan,
            context=context,
            execution_policy_identity=execution_policy_identity,
            shard=shard,
        ),
        payload,
    )


def _resumable_pass(
    state_root: Path,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    shard: ShardWork,
) -> bool:
    path = _state_path(
        state_root,
        plan=plan,
        context=context,
        execution_policy_identity=execution_policy_identity,
        shard=shard,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    identity = payload.pop("evidence_identity", None)
    if identity != _identity(payload):
        return False
    return bool(
        payload
        == _resume_payload(
            plan=plan,
            context=context,
            execution_policy_identity=execution_policy_identity,
            shard=shard,
        )
    )


def _refinement_payload(
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    parent: ShardWork,
    children: tuple[ShardWork, ShardWork],
) -> dict[str, object]:
    return {
        "schema": REFINEMENT_EVIDENCE_SCHEMA,
        "execution_context_identity": context.context_identity,
        "execution_policy_identity": execution_policy_identity,
        "generation_identity": plan.generation_identity,
        "plan_identity": plan.plan_identity,
        "repository_evidence_identity": plan.repository_evidence_identity,
        "producer_artifact_identity": plan.producer_artifact_identity,
        "producer_contract_identity": plan.producer_contract_identity,
        "source_schema": plan.source_schema,
        "parent_shard_id": parent.shard_id,
        "described_work_identity": parent.described_work_identity,
        "parent_work_identity": parent.work_identity,
        "parent_refinement_depth": parent.refinement_depth,
        "children": [
            {
                "shard_id": child.shard_id,
                "physical_work_identity": child.work_identity,
                "parent_work_identity": child.parent_work_identity,
                "refinement_depth": child.refinement_depth,
                "members": list(child.members),
                "pytest_projection_identities": [
                    member.projection_identity for member in child.pytest_members
                ],
                "original_admitted_member_identities": list(
                    child.original_admitted_member_identities
                ),
            }
            for child in children
        ],
    }


def _refinement_state_path(
    state_root: Path,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    parent: ShardWork,
) -> Path:
    key = _identity(
        {
            "schema": REFINEMENT_EVIDENCE_SCHEMA,
            "execution_context_identity": context.context_identity,
            "execution_policy_identity": execution_policy_identity,
            "generation_identity": plan.generation_identity,
            "plan_identity": plan.plan_identity,
            "repository_evidence_identity": plan.repository_evidence_identity,
            "producer_artifact_identity": plan.producer_artifact_identity,
            "producer_contract_identity": plan.producer_contract_identity,
            "parent_work_identity": parent.work_identity,
        }
    ).removeprefix("sha256:")
    return state_root / "refinement" / key[:2] / f"{key}.json"


def _persist_refinement(
    state_root: Path,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    parent: ShardWork,
    children: tuple[ShardWork, ShardWork],
) -> None:
    payload = _refinement_payload(
        plan=plan,
        context=context,
        execution_policy_identity=execution_policy_identity,
        parent=parent,
        children=children,
    )
    payload["evidence_identity"] = _identity(payload)
    _atomic_json(
        _refinement_state_path(
            state_root,
            plan=plan,
            context=context,
            execution_policy_identity=execution_policy_identity,
            parent=parent,
        ),
        payload,
    )


def _resumable_refinement(
    state_root: Path,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    execution_policy_identity: str,
    parent: ShardWork,
) -> tuple[ShardWork, ShardWork] | None:
    if len(parent.members) < 2:
        return None
    path = _refinement_state_path(
        state_root,
        plan=plan,
        context=context,
        execution_policy_identity=execution_policy_identity,
        parent=parent,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    identity = payload.pop("evidence_identity", None)
    if identity != _identity(payload):
        return None
    children = refine_children(parent)
    if payload != _refinement_payload(
        plan=plan,
        context=context,
        execution_policy_identity=execution_policy_identity,
        parent=parent,
        children=children,
    ):
        return None
    return children
