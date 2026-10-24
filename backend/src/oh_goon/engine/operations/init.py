from __future__ import annotations

from typing import Literal, cast

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    BlockedOperationOutcome,
    MissingToolVersion,
    OperationExecution,
    OperationReport,
    ReadinessOperationEvidence,
    ReadyUvTool,
    SuccessfulOperationOutcome,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    LockPresenceState,
    PythonPinReadiness,
    Verdict,
    WorktreeState,
)
from oh_goon.engine.operation_execution_context import OperationContext
from oh_goon.engine.operations.readiness import readiness
from oh_goon.engine.project import policy_snapshot
from oh_goon.engine.reporting import execution, provenance


def run(context: OperationContext) -> OperationExecution:
    started = utc_now()
    policy = policy_snapshot(context.attempt.repository.path)
    checks, python, tools, before_observation = readiness(
        context.attempt.repository.path, policy, context.git, context.cancel_requested
    )
    before = before_observation.fingerprint
    reason = ReasonCode.READY
    verdict = Verdict.READY
    blocked = False

    if (
        checks.project == "missing"
        or not isinstance(tools.uv, ReadyUvTool)
        or isinstance(tools.git, MissingToolVersion)
    ):
        reason, verdict, blocked = ReasonCode.TOOLING_REQUIRED, Verdict.MANUAL_ACTION_REQUIRED, True
    elif (
        checks.working_tree is WorktreeState.NOT_GIT
        or context.git.root(context.attempt.repository.path, context.cancel_requested)
        != context.attempt.repository.path
    ):
        reason, verdict, blocked = (
            ReasonCode.MANUAL_ACTION_REQUIRED,
            Verdict.MANUAL_ACTION_REQUIRED,
            True,
        )
    elif checks.working_tree is WorktreeState.DIRTY:
        reason, verdict, blocked = ReasonCode.DIRTY_WORKTREE, Verdict.MANUAL_ACTION_REQUIRED, True
    elif checks.uv_lock is LockPresenceState.MISSING:
        reason, verdict, blocked = ReasonCode.UV_LOCK_MISSING, Verdict.MANUAL_ACTION_REQUIRED, True
    elif checks.python_pin is PythonPinReadiness.REQUIRED_MISSING:
        reason, verdict, blocked = (
            ReasonCode.MANUAL_ACTION_REQUIRED,
            Verdict.MANUAL_ACTION_REQUIRED,
            True,
        )
    elif policy.values.require_remote and checks.remote.state != "up_to_date":
        reason = (
            ReasonCode.REMOTE_DIVERGED
            if checks.remote.state == "diverged"
            else ReasonCode.REMOTE_AHEAD
            if checks.remote.state == "ahead"
            else ReasonCode.REMOTE_UNAVAILABLE
        )
        verdict, blocked = Verdict.MANUAL_ACTION_REQUIRED, True

    after = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    ).fingerprint
    report = OperationReport(
        capability=context.attempt.capability,
        target=context.attempt.target,
        provenance=provenance(context, started, before, after, policy),
        repository=context.attempt.repository,
        outcome=(
            BlockedOperationOutcome(state="blocked", reason_code=reason)
            if blocked
            else SuccessfulOperationOutcome(
                state="successful",
                verdict=cast(Literal[Verdict.READY, Verdict.UPDATED, Verdict.UNCHANGED], verdict),
                reason_code=reason,
            )
        ),
        evidence=ReadinessOperationEvidence(
            kind="READINESS", readiness=checks, tools=tools, python=python, policy=policy
        ),
    )
    return execution("", "", report)
