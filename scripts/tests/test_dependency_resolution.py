from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling import dependency_resolution_bootstrap as module
from oh_goon.repository_tooling._dependency_lock_portability import (
    backend_lock_portability_issues,
)

ROOT = Path(__file__).resolve().parents[2]


def test_dependency_policy_is_publication_only() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "does not\ninvoke or emulate uv" in source
    for token in ("subprocess", "_run(", "uv sync", "command_sync", "command_lock_convergence_check", "command_lock_check"):
        assert token not in source


def test_public_lock_policy_accepts_public_lock(monkeypatch, capsys) -> None:
    monkeypatch.setattr(module, "backend_lock_portability_issues", lambda _lock: ())
    monkeypatch.setattr(module, "_require_regular", lambda *_args: None)
    monkeypatch.setattr(module, "_sha256", lambda _path: "a" * 64)
    module.command_public_lock_check()
    assert "DEPENDENCY LOCK PUBLICATION POLICY PASSED" in capsys.readouterr().out


def test_public_lock_policy_rejects_repository_private_sources(monkeypatch) -> None:
    monkeypatch.setattr(module, "backend_lock_portability_issues", lambda _lock: ("private registry",))
    monkeypatch.setattr(module, "_require_regular", lambda *_args: None)
    with pytest.raises(module.DependencyResolutionError, match="DEPENDENCY LOCK PORTABILITY FAILED"):
        module.command_public_lock_check()


def test_policy_cli_has_no_native_uv_semantics() -> None:
    assert set(module.parser().parse_args(["public-lock-check"]).__dict__.values()) == {"public-lock-check"}


def test_public_lock_policy_rejects_repository_local_dependency_source(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """
version = 1

[[package]]
name = "hashmarks"
version = "0.21.0"
source = { path = "../vendor/hashmarks-0.21.0-py3-none-any.whl" }

[[package]]
name = "oh-goon"
version = "1267.0.1052"
source = { editable = "." }
""".lstrip(),
        encoding="utf-8",
    )

    issues = backend_lock_portability_issues(lock)

    assert any("repository-local dependency sources are not publishable authority" in issue for issue in issues)
    assert not any("oh-goon" in issue and "editable" in issue for issue in issues)


def test_public_lock_policy_rejects_non_project_editable_dependency(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """
version = 1

[[package]]
name = "hashmarks"
version = "0.25.1"
source = { editable = "../Hashmarks" }
""".lstrip(),
        encoding="utf-8",
    )

    issues = backend_lock_portability_issues(lock)

    assert any("only the Oh-Goon project itself may be an editable local source" in issue for issue in issues)
