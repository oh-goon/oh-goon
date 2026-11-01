from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.verification_gate_control import (
    non_executable_documentation,
)


def test_exact_root_license_is_bounded_non_executable_documentation(
    tmp_path: Path,
) -> None:
    (tmp_path / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    assert non_executable_documentation(tmp_path, "LICENSE")

    nested = tmp_path / "legal" / "LICENSE"
    nested.parent.mkdir()
    nested.write_text("MIT License\n", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "legal/LICENSE")

    (tmp_path / "NOTICE").write_text("Notice\n", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "NOTICE")


def test_license_document_refuses_non_text_or_executable_markers(
    tmp_path: Path,
) -> None:
    license_path = tmp_path / "LICENSE"

    license_path.write_text("MIT License\n\x00", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "LICENSE")

    license_path.write_text("MIT License\n```sh\necho unsafe\n```\n", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "LICENSE")

    license_path.write_text("MIT License\n~~~sh\necho unsafe\n~~~\n", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "LICENSE")
