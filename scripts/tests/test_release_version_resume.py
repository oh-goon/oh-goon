from __future__ import annotations

import shlex
from pathlib import Path

import pytest


def test_set_version_resumes_without_reauthoring_when_native_versions_already_match(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("2.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nversion="2.0.0"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    backend_lock.write_text(
        'version = 1\n[[package]]\nname = "oh-goon"\nversion = "2.0.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    package = frontend / "package.json"
    package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text('{"version":"2.0.0"}\n', encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    monkeypatch.setattr(stamp_release_version, "OPENAPI", tmp_path / "generated/openapi/openapi.json")
    monkeypatch.setattr(stamp_release_version, "GENERATED_FRONTEND", tmp_path / "generated/frontend")
    monkeypatch.setattr(
        stamp_release_version,
        "GOON_TOOL_AUTHORITY",
        backend / "src/oh_goon/canonical_goon_tool_authority.json",
    )
    receipt = stamp_release_version.ReleaseDependencyReceipt(
        backend_lock_sha256=stamp_release_version._sha256_file(backend_lock),
        frontend_lock_sha256=stamp_release_version._sha256_file(package_lock),
    )
    monkeypatch.setattr(
        stamp_release_version, "_assert_release_authoring_prerequisites", lambda: receipt
    )
    monkeypatch.setattr(stamp_release_version, "assert_canonical_source_layout", lambda _root: None)
    monkeypatch.setattr(
        stamp_release_version,
        "observed_versions",
        lambda: {
            "VERSION": "2.0.0",
            "backend/pyproject": "2.0.0",
            "frontend": "2.0.0",
            "openapi": "2.0.0",
        },
    )
    regenerated: list[str] = []
    monkeypatch.setattr(
        stamp_release_version,
        "_regenerate_generated_authority",
        lambda: regenerated.append("generated"),
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> str:
        calls.append(command)
        if command == ("uv", "version", "--short"):
            return "2.0.0"
        if command == ("npm", "pkg", "get", "version"):
            return '"2.0.0"'
        raise AssertionError(f"already-stamped resume unexpectedly invoked {command!r}")

    monkeypatch.setattr(stamp_release_version, "_run", fake_run)

    stamp_release_version.set_version("2.0.0")

    assert calls == [
        ("uv", "version", "--short"),
        ("npm", "pkg", "get", "version"),
    ]
    assert regenerated == ["generated"]


def test_already_stamped_resume_does_not_require_dependency_freshness_reconciliation(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("2.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nname="x"\nversion="2.0.0"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    backend_lock.write_text(
        'version = 1\n[[package]]\nname = "oh-goon"\nversion = "2.0.0"\nsource = { editable = "." }\n'
        '[[package]]\nname="a"\nversion="1.0.0"\nsource = { registry = "https://pypi.org/simple" }\n',
        encoding="utf-8",
    )
    package = frontend / "package.json"
    package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text('{"version":"2.0.0"}\n', encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    monkeypatch.setattr(stamp_release_version, "_backend_version", lambda: "2.0.0")
    monkeypatch.setattr(stamp_release_version, "_frontend_version", lambda: "2.0.0")
    receipt = stamp_release_version.ReleaseDependencyReceipt(
        backend_lock_sha256=stamp_release_version._sha256_file(backend_lock),
        frontend_lock_sha256=stamp_release_version._sha256_file(package_lock),
    )

    assert stamp_release_version._source_identity_matches("2.0.0", receipt) is True


def test_release_version_target_stamps_and_regenerates_without_dependency_materialization() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    body = makefile.split("release-version-set:", 1)[1].split("\n\n", 1)[0]
    stamp_argv = [
        "backend/.venv/bin/oh-goon-release-version",
        "set",
        "$(OH_GOON_RELEASE_VERSION)",
    ]
    assert "backend-dependencies-check" not in body
    assert any(shlex.split(line.lstrip("@-+ ")) == stamp_argv for line in body.splitlines())
    assert "uv sync" not in body
    assert "uv pip install" not in body
    assert "oh-goon-export-openapi" not in body
    assert "oh-goon-generate-api-types" not in body
    assert "oh-goon-generate-goon-tool-authority" not in body
    assert body.count("backend/.venv/bin/") == 1


def test_release_version_transition_rejects_invalid_source_before_any_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("1.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nname="x"\nversion="1.0.0"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    backend_lock.write_text("sentinel\n", encoding="utf-8")
    package = frontend / "package.json"
    package.write_text('{"version":"1.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text('{"version":"1.0.0","packages":{}}\n', encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)

    def reject_source(root: Path) -> None:
        assert root == tmp_path
        raise ValueError("repository source layout is invalid: transport contamination")

    monkeypatch.setattr(stamp_release_version, "assert_canonical_source_layout", reject_source)
    monkeypatch.setattr(
        stamp_release_version,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native authoring must not run after failed source admission")
        ),
    )

    with pytest.raises(ValueError, match="transport contamination"):
        stamp_release_version.set_version("2.0.0")

    assert version.read_text(encoding="utf-8") == "1.0.0\n"
    assert pyproject.read_text(encoding="utf-8").endswith('version="1.0.0"\n')
    assert package.read_text(encoding="utf-8") == '{"version":"1.0.0"}\n'


def test_release_version_transition_rolls_back_version_and_generated_authority_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    generated_frontend = tmp_path / "generated/frontend"
    openapi = tmp_path / "generated/openapi/openapi.json"
    goon_authority = backend / "src/oh_goon/canonical_goon_tool_authority.json"
    generated_frontend.mkdir(parents=True)
    openapi.parent.mkdir(parents=True)
    goon_authority.parent.mkdir(parents=True)
    backend.mkdir(exist_ok=True)
    frontend.mkdir()

    version = tmp_path / "VERSION"
    pyproject = backend / "pyproject.toml"
    backend_lock = backend / "uv.lock"
    package = frontend / "package.json"
    package_lock = frontend / "package-lock.json"
    existing_generated = generated_frontend / "models.ts"
    for path, payload in (
        (version, "1.0.0\n"),
        (pyproject, '[project]\nname="x"\nversion="1.0.0"\n'),
        (backend_lock, "old lock\n"),
        (package, '{"version":"1.0.0"}\n'),
        (package_lock, '{"version":"1.0.0","packages":{}}\n'),
        (openapi, '{"info":{"version":"1.0.0"}}\n'),
        (goon_authority, '{"schema":1}\n'),
        (existing_generated, "old models\n"),
    ):
        path.write_text(payload, encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    monkeypatch.setattr(stamp_release_version, "OPENAPI", openapi)
    monkeypatch.setattr(stamp_release_version, "GENERATED_FRONTEND", generated_frontend)
    monkeypatch.setattr(stamp_release_version, "GOON_TOOL_AUTHORITY", goon_authority)
    incoming_receipt = stamp_release_version.ReleaseDependencyReceipt(
        backend_lock_sha256=stamp_release_version._sha256_file(backend_lock),
        frontend_lock_sha256=stamp_release_version._sha256_file(package_lock),
    )
    monkeypatch.setattr(
        stamp_release_version, "_assert_release_authoring_prerequisites", lambda: incoming_receipt
    )
    monkeypatch.setattr(
        stamp_release_version, "_source_identity_matches", lambda _value, _receipt: False
    )

    def fake_stamp() -> None:
        pyproject.write_text('[project]\nname="x"\nversion="2.0.0"\n', encoding="utf-8")
        package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
        package_lock.write_text('{"version":"2.0.0","packages":{}}\n', encoding="utf-8")

    def fail_generation() -> None:
        openapi.write_text('{"info":{"version":"2.0.0"}}\n', encoding="utf-8")
        existing_generated.write_text("new models\n", encoding="utf-8")
        (generated_frontend / "new.ts").write_text("new\n", encoding="utf-8")
        goon_authority.write_text('{"schema":2}\n', encoding="utf-8")
        raise RuntimeError("simulated generated-authority failure")

    monkeypatch.setattr(stamp_release_version, "stamp", fake_stamp)
    monkeypatch.setattr(stamp_release_version, "assert_public_source_privacy", lambda _root: None)
    monkeypatch.setattr(stamp_release_version, "_regenerate_generated_authority", fail_generation)

    with pytest.raises(RuntimeError, match="generated-authority failure"):
        stamp_release_version.set_version("2.0.0")

    assert version.read_text(encoding="utf-8") == "1.0.0\n"
    assert pyproject.read_text(encoding="utf-8").endswith('version="1.0.0"\n')
    assert backend_lock.read_text(encoding="utf-8") == "old lock\n"
    assert package.read_text(encoding="utf-8") == '{"version":"1.0.0"}\n'
    assert package_lock.read_text(encoding="utf-8") == '{"version":"1.0.0","packages":{}}\n'
    assert openapi.read_text(encoding="utf-8") == '{"info":{"version":"1.0.0"}}\n'
    assert existing_generated.read_text(encoding="utf-8") == "old models\n"
    assert not (generated_frontend / "new.ts").exists()
    assert goon_authority.read_text(encoding="utf-8") == '{"schema":1}\n'


def test_source_identity_match_consumes_captured_dependency_receipt_without_rechecking_portability(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("2.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nname="x"\nversion="2.0.0"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    backend_lock.write_text(
        'version = 1\n[[package]]\nname="a"\nversion="1.0.0"\n'
        'source = { registry = "https://repo.intern.example.test/simple" }\n',
        encoding="utf-8",
    )
    package = frontend / "package.json"
    package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text('{"version":"2.0.0"}\n', encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    monkeypatch.setattr(stamp_release_version, "_backend_version", lambda: "2.0.0")
    monkeypatch.setattr(stamp_release_version, "_frontend_version", lambda: "2.0.0")
    receipt = stamp_release_version.ReleaseDependencyReceipt(
        backend_lock_sha256=stamp_release_version._sha256_file(backend_lock),
        frontend_lock_sha256=stamp_release_version._sha256_file(package_lock),
    )

    # Release version authoring captures exact incoming lock bytes after
    # repository-owned privacy checks.
    # Resume matching transports that receipt instead of rediscovering package-manager state.
    assert stamp_release_version._source_identity_matches("2.0.0", receipt) is True


def test_release_stamp_rejects_preexisting_private_registry_lock_without_rewriting_it(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("2.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nname="x"\nversion="2.0.0"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    original_lock = (
        'version = 1\n[[package]]\nname = "oh-goon"\nversion = "2.0.0"\nsource = { editable = "." }\n'
        '[[package]]\nname="a"\nversion="1.0.0"\n'
        'source = { registry = "https://repo.intern.example.test/simple" }\n'
    )
    backend_lock.write_text(original_lock, encoding="utf-8")
    package = frontend / "package.json"
    package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text('{"version":"2.0.0"}\n', encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)

    calls: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> str:
        calls.append(command)
        if command[:2] == ("uv", "version"):
            return "x 2.0.0 => 2.0.0"
        raise AssertionError(f"private-lock guard should stop before {command!r}")

    monkeypatch.setattr(stamp_release_version, "_run", fake_run)
    with pytest.raises(RuntimeError, match="non-portable before version stamp"):
        stamp_release_version.stamp()

    assert backend_lock.read_text(encoding="utf-8") == original_lock
    assert calls == []


def test_release_stamp_requires_uv_frozen_to_leave_lock_byte_identical(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("2.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nname="x"\nversion="2.0.0"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    original_lock = (
        'version = 1\n[[package]]\nname = "oh-goon"\nversion = "1.9.9"\nsource = { editable = "." }\n'
    )
    backend_lock.write_text(original_lock, encoding="utf-8")
    package = frontend / "package.json"
    package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text('{"version":"2.0.0"}\n', encoding="utf-8")

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> str:
        if command[:2] == ("uv", "version"):
            backend_lock.write_text(original_lock + "# mutated\n", encoding="utf-8")
            return ""
        raise AssertionError(command)

    monkeypatch.setattr(stamp_release_version, "_run", fake_run)
    with pytest.raises(RuntimeError, match="must leave committed resolution authority byte-identical"):
        stamp_release_version.stamp()
    assert backend_lock.read_text(encoding="utf-8") == original_lock
