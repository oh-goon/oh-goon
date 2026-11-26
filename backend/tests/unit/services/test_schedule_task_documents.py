import json
from pathlib import Path

import pytest

from oh_goon.services.schedule_task_documents import (
    ApprovedTaskDocumentError,
    bind_task_document,
    read_approved_tasks,
)


def test_bound_document_accepts_atomic_publication_and_refuses_directory_replacement(
    tmp_path: Path,
) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    path = publication / "approved.json"
    document = bind_task_document(path, protected_roots=())
    assert read_approved_tasks(document) == ()

    staged = publication / "staged.json"
    staged.write_text(json.dumps({"tasks": [{"task_id": "next", "prompt": "Review a change."}]}))
    staged.replace(path)
    (task,) = read_approved_tasks(document)
    assert (task.task_id, task.prompt) == ("next", "Review a change.")

    publication.rename(tmp_path / "previous")
    publication.mkdir()
    path.write_text('{"tasks":[]}')
    with pytest.raises(ApprovedTaskDocumentError, match="directory changed"):
        read_approved_tasks(document)


def test_document_path_cannot_be_in_the_managed_checkout_or_resolve_a_symlink(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    path = checkout / "approved.json"
    with pytest.raises(ApprovedTaskDocumentError, match="outside the writable checkout"):
        bind_task_document(path, protected_roots=(checkout,))

    external = tmp_path / "approved.json"
    document = bind_task_document(external, protected_roots=(checkout,))
    path.write_text('{"tasks":[]}')
    external.symlink_to(path)
    with pytest.raises(ApprovedTaskDocumentError, match="Cannot read"):
        read_approved_tasks(document)


@pytest.mark.parametrize(
    "data",
    [
        b'{"tasks":[],"tasks":[]}',
        b'{"tasks":[{"task_id":"one","prompt":"a"},{"task_id":"one","prompt":"b"}]}',
        b'{"tasks":[{"task_id":"one","prompt":"a","unexpected":true}]}',
        b'{"tasks":[],"note":"\xff"}',
    ],
)
def test_invalid_approved_document_cannot_supply_tasks(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "approved.json"
    path.write_bytes(data)
    document = bind_task_document(path, protected_roots=())
    with pytest.raises(ApprovedTaskDocumentError, match="valid UTF-8 JSON task list"):
        read_approved_tasks(document)
