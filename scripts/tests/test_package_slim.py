from __future__ import annotations

from pathlib import Path
import hashlib
import os
import zipfile

import pytest

from oh_goon.repository_tooling.release import package_slim


@pytest.fixture(autouse=True)
def _valid_dependency_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        package_slim, "assert_public_source_privacy", lambda _root: None
    )


def test_canonical_source_identity_reuses_unchanged_byte_digest_and_invalidates_on_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    source = root / "README.md"
    source.write_text("alpha\n", encoding="utf-8")

    package_slim._canonical_source_identity_cached.cache_clear()
    first = package_slim.canonical_source_identity(root)
    after_first = package_slim._canonical_source_identity_cached.cache_info()
    second = package_slim.canonical_source_identity(root)
    after_second = package_slim._canonical_source_identity_cached.cache_info()

    assert second == first
    assert after_second.hits == after_first.hits + 1

    source.write_text("bravo\n", encoding="utf-8")
    changed = package_slim.canonical_source_identity(root)
    assert changed != first
    assert (
        package_slim._canonical_source_identity_cached.cache_info().misses
        == after_second.misses + 1
    )


def test_shipped_example_bytes_are_packaged_and_bind_source_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    example = root / "examples/standard-atomic/goon.yml"
    example.parent.mkdir(parents=True)
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    original = b"steps:\n  - run: echo original\n"
    example.write_bytes(original)

    before = package_slim.canonical_source_identity(root)
    assert example in package_slim.canonical_source_files(root)
    output = tmp_path / "source.zip"
    package_slim.package_slim(output, root=root)

    with zipfile.ZipFile(output) as archive:
        assert (
            archive.read("oh-goon-1.2.3/examples/standard-atomic/goon.yml") == original
        )
    assert package_slim.archive_source_identity(output) == before

    example.write_bytes(b"steps:\n  - run: echo changed-example\n")
    assert package_slim.canonical_source_identity(root) != before


def test_package_slim_rejects_source_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must-not-leak", encoding="utf-8")
    (root / "leak.txt").symlink_to(outside)
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="refuses symlinks"):
        package_slim.package_slim(tmp_path / "out.zip")

    assert not (tmp_path / "out.zip").exists()


def test_package_slim_writes_complete_archive_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    executable = root / "scripts/run.sh"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"

    digest = package_slim.package_slim(output)

    assert len(digest) == 64
    with zipfile.ZipFile(output) as archive:
        assert archive.read("oh-goon-1.2.3/README.md") == b"hello\n"
        assert (
            archive.getinfo("oh-goon-1.2.3/README.md").compress_type
            == zipfile.ZIP_DEFLATED
        )
        assert (
            archive.getinfo("oh-goon-1.2.3/scripts/run.sh").compress_type
            == zipfile.ZIP_DEFLATED
        )
        mode = archive.getinfo("oh-goon-1.2.3/scripts/run.sh").external_attr >> 16
        assert mode & 0o777 == 0o644
    assert output.stat().st_mode & 0o777 == 0o644
    checksum = tmp_path / "out.zip.sha256"
    assert checksum.read_text(encoding="utf-8") == f"{digest}  out.zip\n"
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    assert checksum.stat().st_mode & 0o777 == 0o644
    assert not list(tmp_path.glob(".out.zip.*.tmp"))
    assert not list(tmp_path.glob(".out.zip.sha256.*.tmp"))


def test_package_slim_refuses_disposable_source_transport_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (root / "README.md:Zone.Identifier").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    (root / "phase.patch").write_text("transport-only patch\n", encoding="utf-8")
    (root / "phase.patch.sha256").write_text("abc  phase.patch\n", encoding="utf-8")
    source = root / "backend/src/pkg/source.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass\n", encoding="utf-8")
    (source.parent / "source.py:Zone.Identifier:$DATA").write_text(
        "[ZoneTransfer]\n", encoding="utf-8"
    )
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"

    with pytest.raises(
        ValueError, match="refuses 4 source transport artifacts"
    ) as exc_info:
        package_slim.package_slim(output)

    assert "Zone.Identifier" not in str(exc_info.value)
    assert "oh-goon-source-layout check --debug" in str(exc_info.value)
    assert not output.exists()


@pytest.mark.parametrize(
    "member",
    [
        "backend/src/pkg/__pycache__/module.cpython-313.pyc",
        ".pytest_cache/v/cache/nodeids",
        "frontend/node_modules/pkg/index.js",
        ".coverage",
    ],
)
def test_archive_source_identity_refuses_transient_or_local_only_payload(
    tmp_path: Path, member: str
) -> None:
    archive_path = tmp_path / "source-transient.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        archive.writestr(f"oh-goon-1.2.3/{member}", "must-not-be-source-identity\n")

    with pytest.raises(
        ValueError, match="forbidden transient or local-only payload"
    ) as exc_info:
        package_slim.archive_source_identity(archive_path)

    assert member not in str(exc_info.value)


def test_archive_source_identity_refuses_source_transport_artifacts(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        archive.writestr(
            "oh-goon-1.2.3/README.md:Zone.Identifier",
            "[ZoneTransfer]\n",
        )

    with pytest.raises(
        ValueError, match="forbidden source transport artifact"
    ) as exc_info:
        package_slim.archive_source_identity(archive_path)

    assert "Zone.Identifier" not in str(exc_info.value)


def test_archive_source_identity_refuses_patch_transport_file(tmp_path: Path) -> None:
    archive_path = tmp_path / "source-patch.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        archive.writestr("oh-goon-1.2.3/phase.patch", "transport-only patch\n")

    with pytest.raises(
        ValueError, match="forbidden source transport artifact"
    ) as exc_info:
        package_slim.archive_source_identity(archive_path)

    assert ".patch" not in str(exc_info.value)


def test_archive_source_identity_refuses_patch_checksum_transport_file(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "source-patch-sha.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        archive.writestr("oh-goon-1.2.3/phase.patch.sha256", "abc  phase.patch\n")

    with pytest.raises(
        ValueError, match="forbidden source transport artifact"
    ) as exc_info:
        package_slim.archive_source_identity(archive_path)

    assert ".patch.sha256" not in str(exc_info.value)


def test_package_slim_is_independent_of_source_executable_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    script = root / "scripts" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    script.chmod(0o755)
    first = tmp_path / "first.zip"
    package_slim.package_slim(first)

    script.chmod(0o644)
    second = tmp_path / "second.zip"
    package_slim.package_slim(second)

    assert first.read_bytes() == second.read_bytes()


def test_package_slim_retains_committed_native_dependency_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\npackage = []\n', encoding="utf-8"
    )
    (root / "frontend/package-lock.json").write_text(
        '{"name":"oh-goon-frontend","version":"1.2.3","lockfileVersion":3,'
        '"packages":{"":{"name":"oh-goon-frontend","version":"1.2.3"}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"

    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "oh-goon-1.2.3/backend/uv.lock" in names
    assert "oh-goon-1.2.3/frontend/package-lock.json" in names


def test_package_slim_retains_blocked_checkpoint_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    marker = root / "CHECKPOINT_NOT_RELEASE.md"
    marker.write_text("not a release\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"

    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        assert (
            archive.read("oh-goon-1.2.3/CHECKPOINT_NOT_RELEASE.md")
            == b"not a release\n"
        )


def test_package_slim_rejects_fifo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    fifo = root / "unexpected.fifo"
    import os

    os.mkfifo(fifo)
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="refuses non-regular filesystem entries"):
        package_slim.package_slim(tmp_path / "out.zip")

    assert not (tmp_path / "out.zip").exists()


def test_package_slim_rejects_symlink_version_before_packaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "version"
    outside.write_text("1.2.3\n", encoding="utf-8")
    (root / "VERSION").symlink_to(outside)
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="VERSION must be a regular non-symlink file"):
        package_slim.package_slim(tmp_path / "out.zip")


def test_package_slim_rejects_noncanonical_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("v1.2\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="canonical x.y.z"):
        package_slim.package_slim(tmp_path / "out.zip")


def test_package_slim_rejects_case_insensitive_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "Thing.txt").write_text("a", encoding="utf-8")
    (root / "thing.txt").write_text("b", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="case-insensitive path collisions"):
        package_slim.package_slim(tmp_path / "out.zip")


@pytest.mark.skipif(
    os.sep == "\\", reason="literal backslash names require POSIX paths"
)
@pytest.mark.parametrize(
    "relative", [r"docs/authored\note.md", r"docs/authored\notes/readme.md"]
)
def test_package_slim_rejects_paths_its_canonical_archive_reader_cannot_represent(
    tmp_path: Path, relative: str
) -> None:
    root = tmp_path / "source"
    source = root / relative
    source.parent.mkdir(parents=True)
    source.write_text("authored\n", encoding="utf-8")
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    output = tmp_path / "source.zip"
    output.write_bytes(b"previous publication")

    with pytest.raises(ValueError, match="canonical POSIX separators"):
        package_slim.package_slim(output, root=root)

    assert output.read_bytes() == b"previous publication"
    assert not output.with_name(output.name + ".sha256").exists()
    assert not list(tmp_path.glob(".source.zip.*.tmp"))


def test_package_slim_rejects_case_folded_file_ancestor_collision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    descendant = root / "docs/guide/a.md"
    descendant.parent.mkdir(parents=True)
    descendant.write_text("descendant\n", encoding="utf-8")
    (root / "docs/Guide").write_text("regular file\n", encoding="utf-8")
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    output = tmp_path / "source.zip"

    with pytest.raises(ValueError, match="file/ancestor path collision"):
        package_slim.package_slim(output, root=root)

    assert not output.exists()


def test_package_slim_rejects_symlink_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    target = tmp_path / "target.zip"
    target.write_bytes(b"keep")
    output = tmp_path / "out.zip"
    output.symlink_to(target)
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="output must not be a symlink"):
        package_slim.package_slim(output)
    assert target.read_bytes() == b"keep"


def test_slim_archive_excludes_frontend_build_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import package_slim as module

    root = tmp_path / "repo"
    (root / "frontend/dist/assets").mkdir(parents=True)
    (root / "frontend/dist/index.html").write_text("generated", encoding="utf-8")
    (root / "frontend/dist/assets/app.js").write_text("generated", encoding="utf-8")
    (root / "frontend/src").mkdir(parents=True)
    (root / "frontend/src/app.ts").write_text("source", encoding="utf-8")
    (root / "generated/release/static").mkdir(parents=True)
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", root)
    output = tmp_path / "source.zip"
    module.package_slim(output)
    import zipfile

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "oh-goon-1.0.0/frontend/src/app.ts" in names
    assert not any("/frontend/dist/" in name for name in names)
    assert not any("/generated/release/static" in name for name in names)


def test_slim_archive_excludes_all_playwright_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    for report in ("playwright-report", "playwright-runtime-report", "test-results"):
        path = root / "frontend" / report / "report.txt"
        path.parent.mkdir(parents=True)
        path.write_text("generated browser evidence", encoding="utf-8")
    source = root / "frontend/e2e/journey.spec.ts"
    source.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "source.zip"

    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "oh-goon-1.0.0/frontend/e2e/journey.spec.ts" in names
    assert not any("report.txt" in name for name in names)


@pytest.mark.parametrize(
    "name",
    ["coverage", "playwright-report", "playwright-runtime-report", "test-results"],
)
def test_package_source_binds_authored_report_names_and_excludes_frontend_outputs(
    tmp_path: Path, name: str
) -> None:
    root = tmp_path / "source"
    authored = root / "docs" / name / "analysis.md"
    authored.parent.mkdir(parents=True)
    authored.write_text("authored\n", encoding="utf-8")
    generated = root / "frontend" / name / "result.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated\n", encoding="utf-8")
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    before = package_slim.canonical_source_identity(root)
    output = tmp_path / "source.zip"

    package_slim.package_slim(output, root=root)

    with zipfile.ZipFile(output) as archive:
        assert archive.read(f"oh-goon-1.2.3/docs/{name}/analysis.md") == b"authored\n"
        assert f"oh-goon-1.2.3/frontend/{name}/result.txt" not in archive.namelist()
    assert package_slim.archive_source_identity(output) == before
    generated.write_text("changed output\n", encoding="utf-8")
    assert package_slim.canonical_source_identity(root) == before
    authored.write_text("changed source\n", encoding="utf-8")
    assert package_slim.canonical_source_identity(root) != before


def test_package_slim_owns_template_certification_fixture_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    fixture = root / "template-certification/fixtures/ml/iris"
    fixture.mkdir(parents=True)
    pyproject = fixture / "pyproject.toml"
    lock = fixture / "uv.lock"
    pyproject.write_text(
        '[project]\nname = "fixture"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    lock.write_text("version = 1\npackage = []\n", encoding="utf-8")

    files = package_slim.canonical_source_files(root)
    relative = {path.relative_to(root).as_posix() for path in files}

    assert relative == {
        "template-certification/fixtures/ml/iris/pyproject.toml",
        "template-certification/fixtures/ml/iris/uv.lock",
    }


def test_package_slim_rejects_unowned_top_level_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=must-not-leak\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="unowned top-level entries: .env"):
        package_slim.package_slim(tmp_path / "out.zip")

    assert not (tmp_path / "out.zip").exists()


def test_package_slim_allows_known_excluded_top_level_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv/local.txt").write_text("ignore me", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    output = tmp_path / "out.zip"
    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        assert not any("/.venv/" in name for name in archive.namelist())


def test_package_slim_allows_but_excludes_hashmarks_derived_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    state = root / ".hashmarks/codemap.sqlite3"
    state.parent.mkdir()
    state.write_text("derived repository intelligence\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    output = tmp_path / "out.zip"
    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        assert not any("/.hashmarks/" in name for name in archive.namelist())


def test_package_slim_allows_but_excludes_known_top_level_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "coverage-python.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"

    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        assert not any(
            name.endswith("/coverage-python.json") for name in archive.namelist()
        )


def test_package_slim_is_a_byte_builder_not_a_second_native_lock_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "backend/uv.lock").write_text("not a real uv lock\n", encoding="utf-8")
    (root / "frontend/package-lock.json").write_text("not json\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    monkeypatch.setattr(
        package_slim, "_require_generated_contract_projection", lambda _root: None
    )
    monkeypatch.setattr(
        package_slim, "require_source_version_coherence", lambda _root, _version: None
    )

    output = tmp_path / "out.zip"
    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "oh-goon-1.2.3/backend/uv.lock" in names
    assert "oh-goon-1.2.3/frontend/package-lock.json" in names


def test_archive_composition_reports_exact_zip_owners(tmp_path: Path) -> None:
    output = tmp_path / "out.zip"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("oh-goon-1.2.3/backend/src/app.py", b"x" * 30)
        archive.writestr("oh-goon-1.2.3/docs/README.md", b"x" * 10)

    total, ranked = package_slim._archive_composition(output)

    assert total == 40
    assert ranked == (("backend", 30), ("docs", 10))


def test_package_slim_rejects_symlink_checksum_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"
    checksum = tmp_path / "out.zip.sha256"
    checksum_target = tmp_path / "checksum-target"
    checksum_target.write_text("keep\n", encoding="utf-8")
    checksum.symlink_to(checksum_target)

    with pytest.raises(ValueError, match="checksum output must not be a symlink"):
        package_slim.package_slim(output)

    assert checksum_target.read_text(encoding="utf-8") == "keep\n"


def test_slim_archive_excludes_repo_local_playwright_browsers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    browser = root / "frontend/.oh-goon-playwright-browsers/chromium/browser"
    browser.parent.mkdir(parents=True)
    browser.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    output = tmp_path / "out.zip"

    package_slim.package_slim(output)

    with zipfile.ZipFile(output) as archive:
        assert not any(
            ".oh-goon-playwright-browsers" in name for name in archive.namelist()
        )


def test_canonical_source_discovery_prunes_excluded_dependency_trees_before_descent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    kept = root / "backend" / "src" / "kept.py"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("kept = True\n", encoding="utf-8")
    ignored = root / "frontend" / "node_modules" / "pkg" / "deep" / "ignored.js"
    ignored.parent.mkdir(parents=True, exist_ok=True)
    ignored.write_text("ignored\n", encoding="utf-8")
    venv_ignored = root / "backend" / ".venv" / "lib" / "python" / "ignored.py"
    venv_ignored.parent.mkdir(parents=True, exist_ok=True)
    venv_ignored.write_text("ignored\n", encoding="utf-8")

    monkeypatch.setattr(
        Path,
        "rglob",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unpruned rglob traversal")
        ),
    )
    files = package_slim.canonical_source_files(root)
    relative = {path.relative_to(root).as_posix() for path in files}
    assert "backend/src/kept.py" in relative
    assert not any("node_modules" in path for path in relative)
    assert not any(".venv" in path for path in relative)


def test_package_slim_rejects_stale_generated_frontend_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    openapi = root / "generated/openapi/openapi.json"
    openapi.parent.mkdir(parents=True)
    openapi.write_text(
        '{"components":{"schemas":{}},"info":{"title":"fixture","version":"1.2.3"},"openapi":"3.1.0","paths":{}}\n',
        encoding="utf-8",
    )
    frontend = root / "generated/frontend"
    frontend.mkdir(parents=True)
    (frontend / "models.ts").write_text("// stale projection\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)

    with pytest.raises(ValueError, match="refuses stale generated contract projection"):
        package_slim.package_slim(tmp_path / "out.zip")

    assert not (tmp_path / "out.zip").exists()


def test_canonical_source_identity_ignores_exact_current_transient_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "VERSION").write_text("1267.0.988\n", encoding="utf-8")
    (root / "backend/src").mkdir(parents=True)
    (root / "backend/src/tracked.py").write_text("authority = True\n", encoding="utf-8")
    monkeypatch.setattr(package_slim, "ROOT", root)
    monkeypatch.setattr(
        package_slim, "assert_public_source_privacy", lambda _root: None
    )
    package_slim._canonical_source_identity_cached.cache_clear()
    before = package_slim.canonical_source_identity(root)
    for relative in (
        ".pytest_cache/state",
        "__pycache__/module.pyc",
        ".hashmarks/index.json",
        "test-results/result.txt",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("transient\n", encoding="utf-8")
    package_slim._canonical_source_identity_cached.cache_clear()
    after = package_slim.canonical_source_identity(root)
    assert after == before
