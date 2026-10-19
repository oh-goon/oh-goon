from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.provenance import OperationProvenance, RepositoryStateFingerprint
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import opaque_identity_token, repository_id, stable_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitBranchState, WorktreeState


def state_id() -> str:
    return stable_id(
        "state",
        {
            "head_sha": None,
            "branch_state": GitBranchState.UNAVAILABLE.value,
            "branch": None,
            "worktree_state": WorktreeState.NOT_GIT.value,
            "pyproject_sha256": None,
            "uv_lock_sha256": None,
        },
    )


def test_opaque_identity_token_uses_the_full_identity_without_parsing_format() -> None:
    first = opaque_identity_token("op-alpha-shared-suffix")
    second = opaque_identity_token("fleet-beta-shared-suffix")

    assert len(first) == 8
    assert first != second
    assert first == opaque_identity_token("op-alpha-shared-suffix")


def test_opaque_identity_token_rejects_invalid_boundaries() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        opaque_identity_token("")
    with pytest.raises(ValueError, match="between 4 and 64"):
        opaque_identity_token("op-test", length=3)


def test_repository_identity_is_stable_for_same_canonical_path(tmp_path: Path) -> None:
    repository = tmp_path / "repos" / "api"
    repository.mkdir(parents=True)

    first = RepositoryIdentity(name="api", path=repository)
    second = RepositoryIdentity(name="different-display-name", path=repository / ".." / "api")

    assert first.repository_id == second.repository_id == repository_id(repository)
    assert first.repository_id.startswith("repo-")


def test_repository_identity_json_roundtrip_preserves_canonical_path(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    identity = RepositoryIdentity(path=repository)

    restored = RepositoryIdentity.model_validate_json(identity.model_dump_json())

    assert restored == identity
    assert restored.path == repository.resolve()


def test_repository_identity_rejects_noncanonical_explicit_id(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    with pytest.raises(ValidationError, match="repository_id must match its canonical path"):
        RepositoryIdentity(repository_id="repo-wrong", name="repo", path=repository)


def test_operation_provenance_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        OperationProvenance(
            operation_id="op-test",
            fleet_run_id=None,
            started_at=datetime(2026, 8, 9, 10, 0, 0),
            finished_at=utc_now(),
        )


def test_operation_provenance_rejects_repository_and_policy_mirrors() -> None:
    now = utc_now()
    with pytest.raises(ValidationError):
        OperationProvenance(
            operation_id="op-test",
            fleet_run_id=None,
            started_at=now,
            finished_at=now,
            repository_id="repo-legacy",
        )
    with pytest.raises(ValidationError):
        OperationProvenance(
            operation_id="op-test",
            fleet_run_id=None,
            started_at=now,
            finished_at=now,
            policy_id="policy-legacy",
        )


def test_operation_provenance_requires_fingerprint_inside_operation_interval() -> None:
    started = utc_now()
    fingerprint = RepositoryStateFingerprint(
        captured_at=started,
        head_sha=None,
        branch_state=GitBranchState.UNAVAILABLE,
        branch=None,
        worktree_state=WorktreeState.NOT_GIT,
        pyproject_sha256=None,
        uv_lock_sha256=None,
    )
    finished = utc_now()
    provenance = OperationProvenance(
        operation_id="op-test",
        fleet_run_id="fleet-test",
        started_at=started,
        finished_at=finished,
        before=fingerprint,
        after=fingerprint,
    )

    assert provenance.before is not None
    assert provenance.before.state_id == state_id()


def test_operation_provenance_rejects_fingerprint_after_finish() -> None:
    started = utc_now()
    finished = utc_now()
    later = finished.replace(microsecond=min(finished.microsecond + 1, 999999))
    if later == finished:
        from datetime import timedelta

        later = finished + timedelta(microseconds=1)
    fingerprint = RepositoryStateFingerprint(
        captured_at=later,
        head_sha=None,
        branch_state=GitBranchState.UNAVAILABLE,
        branch=None,
        worktree_state=WorktreeState.NOT_GIT,
        pyproject_sha256=None,
        uv_lock_sha256=None,
    )
    with pytest.raises(ValidationError):
        OperationProvenance(
            operation_id="op-test",
            fleet_run_id="fleet-test",
            started_at=started,
            finished_at=finished,
            after=fingerprint,
        )


def test_repository_fingerprint_rejects_explicit_derived_state_id() -> None:
    with pytest.raises(ValidationError):
        RepositoryStateFingerprint(
            state_id="state-wrong",
            captured_at=utc_now(),
            head_sha=None,
            branch_state=GitBranchState.UNAVAILABLE,
            branch=None,
            worktree_state=WorktreeState.NOT_GIT,
            pyproject_sha256=None,
            uv_lock_sha256=None,
        )


def test_repository_fingerprint_state_id_is_derived_from_content() -> None:
    fingerprint = RepositoryStateFingerprint(
        captured_at=utc_now(),
        head_sha=None,
        branch_state=GitBranchState.UNAVAILABLE,
        branch=None,
        worktree_state=WorktreeState.NOT_GIT,
        pyproject_sha256=None,
        uv_lock_sha256=None,
    )
    assert fingerprint.state_id == state_id()


def test_stable_id_rejects_non_json_payloads() -> None:
    class UnsupportedIdentityValue:
        pass

    with pytest.raises(TypeError):
        stable_id("test", {"value": UnsupportedIdentityValue()})


def test_stable_id_rejects_non_finite_numbers_and_non_string_object_keys() -> None:
    with pytest.raises(TypeError, match="numbers must be finite"):
        stable_id("test", {"value": float("nan")})
    with pytest.raises(TypeError, match="object keys must be strings"):
        stable_id("test", {1: "numeric-key"})


def test_stable_id_rejects_cyclic_payloads() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(TypeError, match="must not contain cycles"):
        stable_id("test", cyclic)
