"""A reviewed source expectation fences the original admission without re-indexing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.cli_configuration import prepare_configuration
from oh_goon.cli_parser import build_parser
from oh_goon.core.errors import FleetPlanInvalid, ReviewedRepositorySourceStale
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.source_snapshot import source_snapshot_identity
from oh_goon.services import run_configuration_inspection
from oh_goon.services.run_configuration import (
    decode_run_configuration_snapshot,
    encode_run_configuration_snapshot,
    load_run_configuration,
)
from tests.factories.run_configuration import write_run_configuration


def test_source_review_is_explicit_and_reports_the_existing_source_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = write_run_configuration(
        tmp_path,
        monkeypatch,
        goon_content="steps:\n- id: check\n  run: 'true'\n",
    )
    (tmp_path / "repository/service.py").write_text("private_source_sentinel = 1\n")
    native = source_snapshot_identity
    observed: list[Path] = []

    def capture(path: Path) -> SnapshotIdentity:
        observed.append(path)
        return native(path)

    monkeypatch.setattr(run_configuration_inspection, "source_snapshot_identity", capture)
    assert main(["workflow", "inspect", str(selected), "--json"]) == 0
    ordinary = json.loads(capsys.readouterr().out)
    assert "source_authority" not in ordinary["target"]
    assert observed == []

    assert main(["workflow", "inspect", str(selected), "--review-source", "--json"]) == 0
    rendered = capsys.readouterr().out
    report = json.loads(rendered)
    authority = RepositorySourceAuthority.model_validate(report["target"]["source_authority"])
    identity = native(tmp_path / "repository")
    assert authority.source_snapshot == PlanSnapshotEvidence(
        sha256=identity.sha256, byte_count=identity.byte_count, entry_count=identity.entry_count
    )
    assert observed == [tmp_path / "repository"]
    assert "private_source_sentinel" not in rendered
    assert not selected.with_name("goon.lock").exists()
    assert not (tmp_path / "state.sqlite3").exists()

    payload = yaml.safe_load(selected.read_text())
    payload["target"]["reviewed_source"] = authority.model_dump(mode="json")
    selected.write_text(yaml.safe_dump(payload))
    captured = load_run_configuration(selected)
    restored = decode_run_configuration_snapshot(encode_run_configuration_snapshot(captured))
    assert restored.selection.target.reviewed_source == authority
    assert restored.external_artifacts() == (("run.yml", selected.read_bytes()),)

    replacement = tmp_path / "old-repository"
    (tmp_path / "repository").rename(replacement)
    (tmp_path / "repository").mkdir()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("A different reviewed subject cannot acquire workflow source")

    monkeypatch.setattr("oh_goon.cli_configuration.capture_run_configuration_source", forbidden)
    args = build_parser().parse_args(["workflow", "inspect", str(selected), "--json"])
    with pytest.raises(ReviewedRepositorySourceStale, match="reviewed source subject"):
        prepare_configuration(args)


def test_source_review_requires_one_registered_repository_before_content_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = write_run_configuration(
        tmp_path,
        monkeypatch,
        goon_content="steps:\n- id: check\n  run: 'true'\n",
    )
    (tmp_path / "league.yml").write_text("version: 1\nfolders: []\n")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Unregistered source review must not read repository content")

    monkeypatch.setattr(run_configuration_inspection, "source_snapshot_identity", forbidden)
    assert main(["workflow", "inspect", str(selected), "--review-source", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["execution"] == "not_started_by_diagnostic"


def test_reviewed_source_rejects_plural_scope_and_invalid_authority_at_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = write_run_configuration(tmp_path, monkeypatch)
    repository = tmp_path / "repository"
    identity = source_snapshot_identity(repository)
    authority = RepositorySourceAuthority.create(
        subject_instance_id=repository_subject_instance_id(repository),
        source_snapshot=PlanSnapshotEvidence(
            sha256=identity.sha256, byte_count=identity.byte_count, entry_count=identity.entry_count
        ),
    )
    payload = yaml.safe_load(selected.read_text())
    payload["target"] = {
        "repositories": ["repository", "another"],
        "reviewed_source": authority.model_dump(mode="json"),
    }
    selected.write_text(yaml.safe_dump(payload))
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(selected)
    payload["target"] = {
        "repository": "repository",
        "reviewed_source": {**authority.model_dump(mode="json"), "authority_id": "wrong"},
    }
    selected.write_text(yaml.safe_dump(payload))
    with pytest.raises(FleetPlanInvalid):
        load_run_configuration(selected)


@pytest.mark.parametrize("incoming_review", [False, True])
def test_reviewed_source_rejects_workflow_within_target_before_source_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    incoming_review: bool,
) -> None:
    external = write_run_configuration(
        tmp_path,
        monkeypatch,
        goon_content="steps:\n- id: check\n  run: 'true'\n",
    )
    repository = tmp_path / "repository"
    identity = source_snapshot_identity(repository)
    authority = RepositorySourceAuthority.create(
        subject_instance_id=repository_subject_instance_id(repository),
        source_snapshot=PlanSnapshotEvidence(
            sha256=identity.sha256, byte_count=identity.byte_count, entry_count=identity.entry_count
        ),
    )
    payload = yaml.safe_load(external.read_text())
    payload["target"] = {"repository": "."}
    if incoming_review:
        payload["target"]["reviewed_source"] = authority.model_dump(mode="json")
    selected = repository / "run.yml"
    selected.write_text(yaml.safe_dump(payload))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail(
            "Self-referential source review must fail before source acquisition/observation"
        )

    monkeypatch.setattr("oh_goon.cli_configuration.capture_run_configuration_source", forbidden)
    monkeypatch.setattr(run_configuration_inspection, "source_snapshot_identity", forbidden)
    arguments = ["workflow", "inspect", str(selected), "--json"]
    if not incoming_review:
        arguments.append("--review-source")
    assert main(arguments) == 2
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["code"] == "CONFIG_REVIEWED_SOURCE_LAYOUT"
    assert diagnostic["fields"] == ["target.reviewed_source"]
    assert "outside the repository" in diagnostic["safe_next_action"]
    assert not selected.with_name("goon.lock").exists()
