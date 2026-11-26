"""Operator output preserves canonical failure and safely displays peer notes."""

import hashlib
import json
import os
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon import cli_parallel_agents as commands
from oh_goon.cli import main
from oh_goon.cli_parallel_agents import _terminal_note_body, _work_exit_status
from oh_goon.cli_parser import build_parser
from oh_goon.core.models.agent_notes import NOTE_RETENTION_DAYS, AgentNoteAuthor, AgentNoteInput
from oh_goon.core.models.candidate_execution import CandidateWorkspaceAdmission
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.parallel_agent_work import (
    ParallelAgentChild,
    ParallelAgentChildReadback,
    ParallelAgentWorkIntent,
    ParallelAgentWorkReadback,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.product_identity import repository_id, repository_subject_instance_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus
from oh_goon.core.vocabulary.workspace import WorkspaceKind
from oh_goon.runtime.agent_notes import AgentNotesStore, _append_in
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal


def _work(tmp_path: Path) -> ParallelAgentWorkReadback:
    return ParallelAgentWorkReadback(
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


def test_completed_child_needing_attention_does_not_pass_group_launch(tmp_path: Path) -> None:
    assert _work_exit_status(_work(tmp_path)) == 1


@pytest.mark.parametrize("settles", [True, False])
def test_parallel_wait_observes_original_identity_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    settles: bool,
) -> None:
    completed = _work(tmp_path)
    active = completed.model_copy(
        update={
            "dispatch_closed": False,
            "children": (
                completed.children[0],
                ParallelAgentChildReadback(fleet_run_id="task-2", dispatch="unresolved"),
            ),
        }
    )
    database = object()
    reads = []
    elapsed = 0.0

    def existing_database(*, write: bool = False) -> object:
        assert not write
        return database

    def read_work(reader: object, work_id: str) -> ParallelAgentWorkReadback:
        assert reader is database and work_id == "task"
        reads.append(work_id)
        return completed if settles and len(reads) > 1 else active

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(commands, "_existing_database", existing_database)
    monkeypatch.setattr(commands, "read_parallel_work", read_work)
    monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep))
    args = build_parser().parse_args(
        ["runs", "parallel-work", "task", "--wait-seconds", "1", "--json"]
    )
    assert commands.run_parallel_agent_command(args) == (1 if settles else 3)
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "oh-goon.parallel-work-wait/v1"
    assert result["status"] == ("terminal" if settles else "timed_out")
    assert result["work"] == (completed if settles else active).model_dump(mode="json")
    assert reads == ["task"] * (2 if settles else 5)


def test_terminal_note_text_escapes_control_characters_and_indents_replies() -> None:
    assert _terminal_note_body("idea\x1b[31m\nreply\r") == "  idea\\x1b[31m\n  reply\\r"


@pytest.fixture
def note_export_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[LocalDatabase, Path, AgentNoteAuthor]:
    source = tmp_path / "source"
    source.mkdir()
    database = LocalDatabase(tmp_path / "state" / "notes.sqlite")
    monkeypatch.setenv("OH_GOON_DB", str(database.path))
    source_digest = "9" * 64
    intent = ParallelAgentWorkIntent(
        work_id="task",
        repository_id=repository_id(source),
        source=RepositorySourceAuthority.create(
            subject_instance_id=repository_subject_instance_id(source),
            source_snapshot=PlanSnapshotEvidence(
                sha256=source_digest, byte_count=10, entry_count=1
            ),
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
        notes_enabled=True,
        verifier_id="verifier",
    )
    author = AgentNoteAuthor(
        fleet_run_id="task-1",
        repository_id=intent.repository_id,
        step_id="agent",
        attempt_id="attempt-a",
        base_source_sha256=source_digest,
        workspace_id="worktree-a",
    )
    candidate = CandidateWorkspaceAdmission.model_construct(
        parallel_work_id=intent.work_id,
        source=intent.source,
        workspace=WorkspaceRef(
            workspace_id=author.workspace_id,
            repository_id=intent.repository_id,
            kind=WorkspaceKind.WORKTREE,
            base_revision=intent.base_revision,
        ),
    )
    target = FleetPlanTarget.model_construct(candidate_workspace=candidate)
    recorded_run = SimpleNamespace(
        run=SimpleNamespace(
            target=target,
            targets=(SimpleNamespace(path=source, repository_id=intent.repository_id),),
        )
    )
    monkeypatch.setattr(
        Journal,
        "read_view_from_connection",
        lambda *_: SimpleNamespace(
            run_detail=lambda run_id: recorded_run if run_id == "task-1" else None
        ),
    )
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO parallel_agent_work "
            "(work_id, request_hash, intent_json, verifier_json, controller_json) "
            "VALUES ('task', 'request', ?, '{}', ?)",
            (
                intent.model_dump_json(),
                json.dumps(ProcessBirthIdentity(pid=1, boot_id="test", start_ticks=1).to_payload()),
            ),
        )
        connection.executemany(
            "INSERT INTO parallel_agent_children (fleet_run_id, work_id, ordinal) "
            "VALUES (?, 'task', ?)",
            (("task-1", 0), ("task-2", 1)),
        )
        for index, body in enumerate(
            (
                "Check the parser. token=PRIVATE_TOKEN\n# Untrusted heading\x1b",
                "Unselected private body.",
                "Review the source with the original constraints.",
            )
        ):
            _append_in(
                connection,
                intent.work_id,
                author,
                AgentNoteInput(request_id=f"note-{index}", kind="idea", body=body),
            )
    return database, source, author


def test_cli_exports_exact_redacted_selection_to_private_file(
    note_export_state: tuple[LocalDatabase, Path, AgentNoteAuthor],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, source, _ = note_export_state
    destination = tmp_path / "context.md"
    args = [
        "runs",
        "parallel-notes",
        "task",
        "--export-note",
        "3",
        "--export-note",
        "1",
        "--output",
        str(destination),
        "--json",
    ]

    assert main(args) == 0
    text = destination.read_text(encoding="utf-8")
    receipt = json.loads(capsys.readouterr().out)
    assert receipt.pop("_cli")["exit_code"] == 0
    assert receipt == {
        "destination": str(destination),
        "note_ids": [1, 3],
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "byte_count": len(text.encode()),
    }
    assert "UNTRUSTED_AGENT_CONTEXT" in text
    assert "Task: task" in text and f"Git base: {'7' * 40}" in text
    assert f"Captured source SHA-256: {'9' * 64}" in text
    assert f"Author task SHA-256: {'1' * 64}" in text
    assert 'Author Run: "task-1"' in text and 'Candidate workspace: "worktree-a"' in text
    assert "    Check the parser. token=[REDACTED]\n    # Untrusted heading\\x1b" in text
    assert "PRIVATE_TOKEN" not in text and "Unselected private body." not in text
    assert str(source) not in text
    assert text.index("## Note 1") < text.index("## Note 3")
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert main(args) == 2
    assert destination.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    "condition",
    ["missing", "duplicate", "expired", "oversize", "source", "state", "symlink", "paging"],
)
def test_cli_note_export_refuses_invalid_selection_or_destination(
    note_export_state: tuple[LocalDatabase, Path, AgentNoteAuthor],
    tmp_path: Path,
    condition: str,
) -> None:
    database, source, author = note_export_state
    output = tmp_path / "context.md"
    args = ["runs", "parallel-notes", "task", "--export-note", "1"]
    if condition == "missing":
        args += ["--export-note", "4"]
    elif condition == "duplicate":
        args += ["--export-note", "1"]
    elif condition == "expired":
        with database.transaction(write=True) as connection:
            AgentNotesStore(database, Journal(database)).settle_in(
                connection, "task", settled_at=utc_now() - timedelta(days=NOTE_RETENTION_DAYS + 1)
            )
    elif condition == "oversize":
        with database.transaction(write=True) as connection:
            for index in range(5):
                note = AgentNoteInput(request_id=f"large-{index}", kind="idea", body="x" * 4096)
                note_id = _append_in(connection, "task", author, note).note_id
                args += ["--export-note", str(note_id)]
    elif condition == "source":
        output = source / "context.md"
    elif condition == "state":
        output = database.path.parent / "context.md"
    elif condition == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        output = alias / "context.md"
    else:
        args += ["--after", "0"]

    assert main([*args, "--output", os.fspath(output)]) == 2
    assert not output.exists()
