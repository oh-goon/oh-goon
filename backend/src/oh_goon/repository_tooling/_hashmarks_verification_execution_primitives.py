from __future__ import annotations

from dataclasses import dataclass

from oh_goon.runtime.execution.shard_models import ShardExecutionReport, ShardResult


@dataclass(frozen=True, slots=True)
class VerificationExecutionCoverage:
    attempted: tuple[str, ...]
    resumed: tuple[str, ...]
    completed: tuple[str, ...]
    missing: tuple[str, ...]
    duplicates: tuple[str, ...]
    extras: tuple[str, ...]
    lineage: tuple[dict[str, object], ...]
    terminal_success: bool


def _walk_results(results: tuple[ShardResult, ...]) -> tuple[ShardResult, ...]:
    rows: list[ShardResult] = []

    def visit(result: ShardResult) -> None:
        rows.append(result)
        for child in result.children:
            visit(child)

    for result in results:
        visit(result)
    return tuple(rows)


def _completed_leaf_members(result: ShardResult) -> tuple[str, ...]:
    if result.children:
        return tuple(member for child in result.children for member in _completed_leaf_members(child))
    return result.members if result.passed else ()


def _attempt_payload(result: ShardResult) -> list[dict[str, object]]:
    return [
        {
            "attempt_identity": attempt.attempt_identity,
            "attempt": attempt.attempt,
            "status": attempt.status,
            "retry_admitted": attempt.retry_admitted,
            "retry_reason": attempt.retry_reason,
        }
        for attempt in result.attempts
    ]


def _lineage_row(result: ShardResult) -> dict[str, object]:
    return {
        "shard_id": result.shard_id,
        "described_work_identity": result.described_work_identity,
        "work_identity": result.work_identity,
        "parent_work_identity": result.parent_work_identity,
        "refinement_depth": result.refinement_depth,
        "members": list(result.members),
        "pytest_projection_identities": [member.projection_identity for member in result.pytest_members],
        "status": result.status,
        "resumed": result.resumed,
        "attempts": _attempt_payload(result),
    }


def build_execution_coverage(
    selected: tuple[str, ...],
    report: ShardExecutionReport,
) -> VerificationExecutionCoverage:
    walked = _walk_results(report.results)
    attempted = tuple(sorted({member for result in walked if result.attempts for member in result.members}))
    resumed = tuple(sorted({member for result in walked if result.resumed for member in result.members}))
    completed_raw = tuple(member for result in report.results for member in _completed_leaf_members(result))
    counts: dict[str, int] = {}
    for member in completed_raw:
        counts[member] = counts.get(member, 0) + 1
    selected_set, completed_set = set(selected), set(completed_raw)
    observed_set = {member for result in walked if result.attempts or result.resumed for member in result.members}
    missing = tuple(sorted(selected_set - completed_set))
    extras = tuple(sorted(observed_set - selected_set))
    duplicates = tuple(sorted(member for member, count in counts.items() if count > 1))
    return VerificationExecutionCoverage(
        attempted=attempted,
        resumed=resumed,
        completed=tuple(sorted(completed_raw)),
        missing=missing,
        duplicates=duplicates,
        extras=extras,
        lineage=tuple(_lineage_row(result) for result in walked),
        terminal_success=bool(report.passed and not missing and not extras and not duplicates),
    )
