"""Publish an explicit selection of operator-redacted task notes for review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_notes import NOTE_PAGE_BYTES, AgentNotesError
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.exact_file_write import (
    ExactPublicationDurabilityError,
    prepared_sibling_directory,
    publish_prepared_file,
)
from oh_goon.runtime.agent_notes import AgentNotesStore, SelectedAgentNotes
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal


def _render_context(selected: SelectedAgentNotes) -> str:
    intent = selected.intent
    source = intent.source.source_snapshot.sha256
    tasks = {child.fleet_run_id: child.task_sha256 for child in intent.children}
    lines = [
        "# Shared task notes",
        "",
        "Authority: `UNTRUSTED_AGENT_CONTEXT`",
        "",
        "These are attributed notes for review. A new Run independently establishes its source,",
        "permissions, and verification. Candidate bytes at the time of each note are unbound.",
        "",
        "## Original task",
        "",
        f"    Task: {intent.work_id}",
        f"    Git base: {intent.base_revision}",
        f"    Captured source SHA-256: {source}",
    ]
    for note in selected.notes:
        receipt = note.receipt
        author = receipt.author
        lines.extend(
            (
                "",
                f"## Note {receipt.note_id}",
                "",
                f"    Kind: {receipt.kind}",
                f"    Author Run: {json.dumps(author.fleet_run_id)}",
                f"    Author task SHA-256: {tasks[author.fleet_run_id]}",
                f"    Step: {json.dumps(author.step_id)}",
                f"    Attempt: {json.dumps(author.attempt_id)}",
                f"    Candidate workspace: {json.dumps(author.workspace_id)}",
            )
        )
        if receipt.reply_to is not None:
            lines.append(f"    Reply to note: {receipt.reply_to}")
        lines.append("")
        # Render the body literally so links, HTML and headings are inert in Markdown.
        visible = "".join(
            char if char.isprintable() or char == "\n" else ascii(char)[1:-1] for char in note.body
        )
        lines.extend(f"    {line}" for line in visible.split("\n"))
    return "\n".join(lines) + "\n"


def export_parallel_notes(
    database: LocalDatabase,
    work_id: str,
    note_ids: tuple[int, ...],
    destination: Path,
) -> dict[str, object]:
    """Export redacted notes to one new private artifact without changing Run state."""
    selected = AgentNotesStore(database, Journal(database)).select_for_export(work_id, note_ids)
    target = destination.expanduser().absolute()
    state = ApplicationStatePathLayout.from_database(database.path)
    if (
        ".." in target.parts
        or target.exists()
        or not target.parent.is_dir()
        or any(item.is_symlink() for item in (target, *target.parents))
        or any(target.is_relative_to(root) for root in (selected.source_path, state.root))
    ):
        raise FleetPlanInvalid("Note export requires a new file outside source and execution state")
    document = _render_context(selected)
    payload = document.encode("utf-8")
    if len(payload) > NOTE_PAGE_BYTES:
        raise AgentNotesError("note_export_too_large")
    try:
        with prepared_sibling_directory(target) as staging:
            staging.write_text("notes.md", document)
            publish_prepared_file(staging.path / "notes.md", target)
    except ExactPublicationDurabilityError:
        raise AgentNotesError("note_export_publication_unverified") from None
    return {
        "destination": str(target),
        "note_ids": [note.receipt.note_id for note in selected.notes],
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
    }
