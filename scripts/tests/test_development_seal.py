from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release.development_seal import (
    DevelopmentSealCollision,
    publish_development_seal,
)


def _payload(*, candidate: str = "b") -> dict[str, object]:
    return {
        "schema": "oh-goon.development-seal/v1",
        "version": "7.8.9",
        "status": "DEVELOPMENT",
        "parent": {
            "version": "7.8.8",
            "source_identity": "sha256:" + "1" * 64,
            "zip_sha256": "2" * 64,
        },
        "candidate": {
            "source_identity": "sha256:" + candidate * 64,
            "zip_sha256": "4" * 64,
        },
        "patch": {
            "from_version": "7.8.8",
            "to_version": "7.8.9",
            "sha256": "5" * 64,
        },
    }


def test_development_seal_publish_is_immutable_and_idempotent(tmp_path: Path) -> None:
    payload = _payload()
    path = publish_development_seal(tmp_path, payload)
    original = path.read_bytes()

    assert publish_development_seal(tmp_path, payload) == path
    assert path.read_bytes() == original
    assert json.loads(original)["candidate"]["source_identity"] == "sha256:" + "b" * 64


def test_development_seal_refuses_same_version_with_different_identity(tmp_path: Path) -> None:
    publish_development_seal(tmp_path, _payload(candidate="b"))

    with pytest.raises(DevelopmentSealCollision, match="allocate the next version"):
        publish_development_seal(tmp_path, _payload(candidate="c"))


def test_development_seal_refuses_lineage_mismatch_before_write(tmp_path: Path) -> None:
    payload = _payload()
    payload["patch"]["from_version"] = "7.8.7"  # type: ignore[index]

    with pytest.raises(ValueError, match="patch lineage"):
        publish_development_seal(tmp_path, payload)

    assert list(tmp_path.iterdir()) == []


def test_development_seal_refuses_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("historical\n", encoding="utf-8")
    seal = tmp_path / "OH-GOON-7.8.9-DEVELOPMENT-SEAL.json"
    seal.symlink_to(target)

    with pytest.raises(DevelopmentSealCollision, match="symlink"):
        publish_development_seal(tmp_path, _payload())

    assert target.read_text(encoding="utf-8") == "historical\n"
