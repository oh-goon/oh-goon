"""Source and initial candidate identities cross admission as one bounded fact."""

from __future__ import annotations

import stat

import pytest

from oh_goon.core.models.candidate_execution import CandidateWorkspaceAdmission
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceRef
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary.workspace import WorkspaceKind


def _admission() -> CandidateWorkspaceAdmission:
    source = RepositorySourceAuthority.create(
        subject_instance_id="repo-subject-instance-" + "a" * 64,
        source_snapshot=PlanSnapshotEvidence(sha256="b" * 64, byte_count=7, entry_count=1),
    )
    read = WorkspaceRef(
        workspace_id="source",
        repository_id="registered-repository",
        base_revision="captured-revision",
        kind=WorkspaceKind.READ_SNAPSHOT,
    )
    candidate = WorkspaceRef(
        workspace_id="candidate",
        repository_id=read.repository_id,
        base_revision=read.base_revision,
        kind=WorkspaceKind.WORKTREE,
    )
    checkpoint = CandidateCheckpoint(
        checkpoint_id="initial",
        workspace_id=candidate.workspace_id,
        source_sha256=source.source_snapshot.sha256,
        byte_count=source.source_snapshot.byte_count,
        entry_count=source.source_snapshot.entry_count,
        created_at=utc_now(),
    )
    return CandidateWorkspaceAdmission(
        fleet_run_id="fresh-run",
        source=source,
        source_workspace=read,
        workspace=candidate,
        checkpoint=checkpoint,
        state_version=2,
        workspace_device=1,
        workspace_inode=2,
        workspace_mode=stat.S_IFDIR | 0o700,
        workspace_ctime_ns=1_790_000_000_000_000_001,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("workspace_id", "another-candidate"),
        ("source_sha256", "c" * 64),
        ("byte_count", 8),
        ("entry_count", 2),
        ("parent_checkpoint_id", "prior-attempt"),
        ("attempt_id", "historical-attempt"),
        ("assignment_id", "historical-assignment"),
    ],
)
def test_initial_candidate_cannot_adopt_another_checkpoint_or_source(field, replacement):
    data = _admission().model_dump(mode="json")
    data["checkpoint"][field] = replacement
    with pytest.raises(ValueError, match="exact initial captured source"):
        CandidateWorkspaceAdmission.model_validate(data)


@pytest.mark.parametrize("field", ["repository_id", "base_revision", "workspace_id"])
def test_candidate_and_read_source_allocation_keep_their_distinct_scope(field):
    data = _admission().model_dump(mode="json")
    data["source_workspace"][field] = (
        data["workspace"][field] if field == "workspace_id" else "foreign-source"
    )
    with pytest.raises(ValueError, match="exact initial captured source"):
        CandidateWorkspaceAdmission.model_validate(data)


def test_candidate_admission_requires_a_directory_physical_binding():
    data = _admission().model_dump(mode="json")
    data["workspace_mode"] = stat.S_IFREG | 0o600
    with pytest.raises(ValueError, match="exact initial captured source"):
        CandidateWorkspaceAdmission.model_validate(data)


def test_nanosecond_directory_token_keeps_exact_value_across_json_api_transport():
    admission = _admission()
    assert admission.model_dump(mode="json")["workspace_ctime_ns"] == "1790000000000000001"
    restored = CandidateWorkspaceAdmission.model_validate_json(admission.model_dump_json())
    assert restored.workspace_ctime_ns == 1_790_000_000_000_000_001
    assert restored == admission


def test_nanosecond_directory_token_uses_one_exact_decimal_string_api_schema():
    validation = CandidateWorkspaceAdmission.model_json_schema(mode="validation")
    serialization = CandidateWorkspaceAdmission.model_json_schema(mode="serialization")
    assert validation == serialization
    assert validation["properties"]["workspace_ctime_ns"] == {
        "type": "string",
        "pattern": "^[0-9]+$",
        "title": "Workspace Ctime Ns",
    }
