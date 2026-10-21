from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release import stamp_release_version


def _release_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ...]:
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()

    version = tmp_path / "VERSION"
    pyproject = backend / "pyproject.toml"
    backend_lock = backend / "uv.lock"
    package = frontend / "package.json"
    package_lock = frontend / "package-lock.json"
    openapi = tmp_path / "generated/openapi/openapi.json"
    generated_frontend = tmp_path / "generated/frontend"
    goon_authority = backend / "src/oh_goon/canonical_goon_tool_authority.json"

    version.write_text("1.2.3\n", encoding="utf-8")
    pyproject.write_text(
        '[project]\nname="oh-goon"\nversion="1.2.3"\n', encoding="utf-8"
    )
    backend_lock.write_text("version = 1\npackage = []\n", encoding="utf-8")
    package.write_text(
        '{"name":"oh-goon-frontend","version":"1.2.3"}\n', encoding="utf-8"
    )
    package_lock.write_text(
        '{"lockfileVersion":3,"packages":{"":{"name":"oh-goon-frontend","version":"1.2.3"}}}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    monkeypatch.setattr(stamp_release_version, "OPENAPI", openapi)
    monkeypatch.setattr(stamp_release_version, "GENERATED_FRONTEND", generated_frontend)
    monkeypatch.setattr(stamp_release_version, "GOON_TOOL_AUTHORITY", goon_authority)
    monkeypatch.setattr(
        stamp_release_version, "assert_canonical_source_layout", lambda _root: None
    )
    monkeypatch.setattr(
        stamp_release_version,
        "assert_public_source_privacy",
        lambda _root: None,
        raising=False,
    )

    return version, pyproject, backend_lock, package, package_lock


def test_release_prerequisites_do_not_redecide_native_lock_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _version, _pyproject, backend_lock, _package, package_lock = _release_tree(
        tmp_path, monkeypatch
    )

    def forbidden_native_lock_redecision(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "release version authoring must not run dependency lock qualification"
        )

    # This deliberately shadows the M88 imported helper. M89 must not consult it.
    monkeypatch.setattr(
        stamp_release_version,
        "dependency_lock_issues",
        forbidden_native_lock_redecision,
        raising=False,
    )

    receipt = stamp_release_version._assert_release_authoring_prerequisites()

    assert (
        receipt.backend_lock_sha256
        == hashlib.sha256(backend_lock.read_bytes()).hexdigest()
    )
    assert (
        receipt.frontend_lock_sha256
        == hashlib.sha256(package_lock.read_bytes()).hexdigest()
    )


def test_release_post_authoring_receipt_does_not_revalidate_frontend_lock_with_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _version, _pyproject, backend_lock, _package, package_lock = _release_tree(
        tmp_path, monkeypatch
    )
    incoming = stamp_release_version.ReleaseDependencyReceipt(
        backend_lock_sha256=hashlib.sha256(backend_lock.read_bytes()).hexdigest(),
        frontend_lock_sha256=hashlib.sha256(package_lock.read_bytes()).hexdigest(),
    )

    def forbidden_native_lock_redecision(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "post-authoring receipt must not rerun npm lock qualification"
        )

    # This deliberately shadows the M88 imported helper. M89 must not consult it.
    monkeypatch.setattr(
        stamp_release_version,
        "frontend_dependency_lock_issues",
        forbidden_native_lock_redecision,
        raising=False,
    )

    package_lock.write_text(
        '{"lockfileVersion":3,"packages":{"":{"name":"oh-goon-frontend","version":"2.0.0"}}}\n',
        encoding="utf-8",
    )
    receipt = stamp_release_version._post_authoring_dependency_receipt(incoming)

    assert receipt.backend_lock_sha256 == incoming.backend_lock_sha256
    assert (
        receipt.frontend_lock_sha256
        == hashlib.sha256(package_lock.read_bytes()).hexdigest()
    )


def test_release_native_version_owner_failure_still_rolls_back_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, pyproject, backend_lock, package, package_lock = _release_tree(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        stamp_release_version,
        "dependency_lock_issues",
        lambda _root: (),
        raising=False,
    )
    originals = {
        path: path.read_bytes()
        for path in (version, pyproject, backend_lock, package, package_lock)
    }

    def fail_native_owner(command: tuple[str, ...], *, cwd: Path) -> str:
        assert cwd == tmp_path / "backend"
        assert command == ("uv", "version", "--frozen", "2.0.0")
        raise RuntimeError("native uv version owner rejected transition")

    monkeypatch.setattr(stamp_release_version, "_run", fail_native_owner)

    with pytest.raises(
        RuntimeError, match="native uv version owner rejected transition"
    ):
        stamp_release_version.set_version("2.0.0")

    assert {path: path.read_bytes() for path in originals} == originals
