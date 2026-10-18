"""M136: prove artifact evidence and strict content retention share one owner."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_evidence import MissingPlanArtifact, PresentPlanArtifact
from oh_goon.core.models.fleet_plan_step_primitives import PlanArtifactDeclaration
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations.fleet_plan_step_publication import (
    collect_direct_artifacts,
    collect_strict_artifacts,
)

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    return repository


def _prepared(store: object | None):
    def owned(path: Path, *, owned_root: Path, label: str) -> RepositoryScopedPath:
        return RepositoryScopedPath.create(
            owner_repository_id=OWNER,
            root=owned_root,
            path=path,
            label=label,
        )

    return SimpleNamespace(content_store=store, retained_content_path=owned)


def test_m136_default_evidence_reports_identity_without_retaining_bytes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "proof.json").write_bytes(b'{"success":true}\n')
    declarations = (PlanArtifactDeclaration(path="proof.json"),)
    assert declarations[0].retention == "evidence"
    (result,) = collect_direct_artifacts(repository, declarations)
    assert isinstance(result, PresentPlanArtifact)
    assert result.sha256 == hashlib.sha256(b'{"success":true}\n').hexdigest()
    assert result.byte_count == len(b'{"success":true}\n')
    assert result.retained_object is None


def test_m136_missing_file_is_missing_evidence_not_retained_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (result,) = collect_strict_artifacts(
        repository,
        (PlanArtifactDeclaration(path="missing.zip", retention="content_store"),),
        _prepared(None),
    )
    assert isinstance(result, MissingPlanArtifact)
    assert result.path == "missing.zip"


def test_m136_strict_publisher_sends_owned_file_and_reports_retained_evidence(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    payload = b"identical-release-bytes\n"
    (repository / "release.zip").write_bytes(payload)

    # Publication boundary test double; actual content-store digest, staging,
    # references and retention are separately exercised by existing store tests.
    class CapturingStore:
        published: list[Path]

        def __init__(self) -> None:
            self.published = []

        def put_file(self, scoped: RepositoryScopedPath):
            scoped.prove_owned()
            assert scoped.owner_repository_id == OWNER
            assert scoped.root == repository
            assert scoped.path.read_bytes() == payload
            self.published.append(scoped.path)
            identity = SimpleNamespace(
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_count=len(payload),
                entry_count=1,
            )
            return SimpleNamespace(
                reference_id="ref-" + "a" * 32,
                owner_repository_id=OWNER,
                kind="file",
                identity=identity,
            )

    store = CapturingStore()
    declaration = PlanArtifactDeclaration(path="release.zip", retention="content_store")
    (result,) = collect_strict_artifacts(repository, (declaration,), _prepared(store))
    assert isinstance(result, PresentPlanArtifact)
    assert result.retained_object is not None
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.retained_object.sha256 == result.sha256
    assert result.retained_object.byte_count == result.byte_count == len(payload)
    assert result.retained_object.owner_repository_id == OWNER
    assert store.published == [repository / "release.zip"]


@pytest.mark.parametrize("create_file", [False, True])
def test_m136_direct_mode_never_downgrades_content_store_to_evidence(
    tmp_path: Path, create_file: bool
) -> None:
    repository = _repository(tmp_path)
    if create_file:
        (repository / "release.zip").write_bytes(b"bytes")
    with pytest.raises(FleetPlanInvalid, match="Direct Fleet execution cannot publish"):
        collect_direct_artifacts(
            repository, (PlanArtifactDeclaration(path="release.zip", retention="content_store"),)
        )


def test_m136_strict_mode_refuses_missing_store_before_claiming_retention(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "release.zip").write_bytes(b"bytes")
    with pytest.raises(FleetPlanInvalid, match="without a strict run store"):
        collect_strict_artifacts(
            repository,
            (PlanArtifactDeclaration(path="release.zip", retention="content_store"),),
            _prepared(None),
        )


def test_m136_invalid_retention_cannot_enter_resolved_artifact_contract() -> None:
    with pytest.raises(ValueError):
        PlanArtifactDeclaration(path="a.zip", retention="forever")  # type: ignore[arg-type]
    evidence = PlanArtifactDeclaration(path="a.zip", retention="evidence")
    retained = PlanArtifactDeclaration(path="a.zip", retention="content_store")
    assert evidence.model_dump(mode="json") != retained.model_dump(mode="json")
