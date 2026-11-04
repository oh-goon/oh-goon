from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.verification_gate_control import (
    non_executable_documentation,
)


@pytest.mark.parametrize(
    "text", ("MIT License\n", "MIT License\n```sh\necho example\n```\n")
)
def test_only_root_license_uses_documentation_owner(
    tmp_path: Path,
    text: str,
) -> None:
    (tmp_path / "LICENSE").write_text(text, encoding="utf-8")
    assert non_executable_documentation(tmp_path, "LICENSE")

    nested = tmp_path / "legal" / "LICENSE"
    nested.parent.mkdir()
    nested.write_text("MIT License\n", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "legal/LICENSE")

    (tmp_path / "NOTICE").write_text("Notice\n", encoding="utf-8")
    assert not non_executable_documentation(tmp_path, "NOTICE")
