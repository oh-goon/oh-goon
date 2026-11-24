from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from oh_goon.core.models.agent_notes import (
    AgentNote,
    AgentNoteAuthor,
    AgentNotePage,
    AgentNoteReceipt,
    AgentNotesError,
)
from oh_goon.core.ports.agent_notes import AgentNotesTool
from oh_goon.integrations.agent_pi_notes import PiNoteChannel


def test_pi_notes_requires_registration_and_acknowledges_only_offered_notes() -> None:
    notes = Mock(spec=AgentNotesTool)
    channel = PiNoteChannel(notes)
    assert not channel.ready
    with pytest.raises(AgentNotesError, match="notes_not_ready"):
        channel.request({"operation": "read"})
    notes.read.assert_not_called()

    assert channel.request({"operation": "ready"}) == {"ready": True}
    notes.require_active.assert_called_once()
    assert channel.ready
    notes.read.return_value = AgentNotePage(
        notes=(
            AgentNote(
                receipt=AgentNoteReceipt(
                    note_id=2,
                    request_id="published",
                    kind="observation",
                    author=AgentNoteAuthor(
                        fleet_run_id="peer",
                        repository_id="registered",
                        step_id="agent",
                        attempt_id="peer-attempt",
                        base_source_sha256="b" * 64,
                        workspace_id="peer-worktree",
                    ),
                ),
                body="A peer observation at a distinct candidate.",
            ),
        ),
        last_cursor=2,
    )
    page = channel.request({"operation": "read", "after": 1})
    notes.read.assert_called_once_with(1)
    assert page["authority"] == "UNTRUSTED_AGENT_CONTEXT"
    assert page["observed_candidate_bytes"] == "unbound"
    assert page["notes"] == notes.read.return_value.model_dump(mode="json")["notes"]
    with pytest.raises(AgentNotesError, match="unknown_note_transfer"):
        channel.request({"operation": "acknowledge", "token": "1" * 32})
    notes.acknowledge.assert_not_called()
    assert channel.request({"operation": "acknowledge", "token": page["token"]}) == {
        "acknowledged": True
    }
    notes.acknowledge.assert_called_once_with((2,))
    with pytest.raises(AgentNotesError, match="unknown_note_transfer"):
        channel.request({"operation": "acknowledge", "token": page["token"]})


def test_pi_note_requests_cannot_author_their_identity_or_supply_paths() -> None:
    channel = PiNoteChannel(Mock(spec=AgentNotesTool))
    channel.request({"operation": "ready"})
    admitted = {"request_id": "one", "kind": "idea", "body": "One short idea."}
    for extra in ({"fleet_run_id": "peer"}, {"source_file": "../../sibling"}):
        with pytest.raises(ValidationError):
            channel.request({"operation": "publish", "note": {**admitted, **extra}})
    with pytest.raises(ValidationError, match="bounded UTF-8"):
        channel.request({"operation": "publish", "note": {**admitted, "body": "π" * 2049}})
