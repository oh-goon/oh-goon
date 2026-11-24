"""Operator output preserves canonical failure and safely displays peer notes."""

from pathlib import Path

from oh_goon.cli_parallel_agents import _terminal_note_body, _work_exit_status
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.parallel_agent_work import (
    ParallelAgentChild,
    ParallelAgentChildReadback,
    ParallelAgentWorkIntent,
    ParallelAgentWorkReadback,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


def test_completed_child_needing_attention_does_not_pass_group_launch(tmp_path: Path) -> None:
    readback = ParallelAgentWorkReadback(
        intent=ParallelAgentWorkIntent(
            work_id="task",
            repository_id="repo",
            source=RepositorySourceAuthority.create(
                subject_instance_id=repository_subject_instance_id(tmp_path),
                source_snapshot=PlanSnapshotEvidence(sha256="9" * 64, byte_count=1, entry_count=1),
            ),
            base_revision="7" * 40,
            children=(
                ParallelAgentChild(
                    fleet_run_id="task-1", task_sha256="1" * 64, native_selection_id="a"
                ),
                ParallelAgentChild(
                    fleet_run_id="task-2", task_sha256="2" * 64, native_selection_id="b"
                ),
            ),
            notes_enabled=False,
            verifier_id="verifier",
        ),
        dispatch_closed=True,
        cancelled=False,
        children=(
            ParallelAgentChildReadback(
                fleet_run_id="task-1",
                dispatch="admitted",
                status=FleetRunStatus.COMPLETED,
                outcome=FleetRunOutcome.SUCCESS,
            ),
            ParallelAgentChildReadback(
                fleet_run_id="task-2",
                dispatch="admitted",
                status=FleetRunStatus.COMPLETED,
                outcome=FleetRunOutcome.ATTENTION_REQUIRED,
            ),
        ),
    )
    assert _work_exit_status(readback) == 1


def test_terminal_note_text_escapes_control_characters_and_indents_replies() -> None:
    assert _terminal_note_body("idea\x1b[31m\nreply\r") == "  idea\\x1b[31m\n  reply\\r"
