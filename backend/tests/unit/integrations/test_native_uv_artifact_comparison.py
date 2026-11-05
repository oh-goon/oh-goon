from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.integrations import native_uv_lock as owner


def _manifest(version: str) -> bytes:
    return f"""[project]
name = "artifact-fixture"
version = "1"
requires-python = ">=3.12"
dependencies = ["hashmarks=={version}", "direct-package==1"]
[project.optional-dependencies]
extra = ["extra-package==1"]
[dependency-groups]
quality = ["quality-package==1"]
[build-system]
requires = []
build-backend = "must_never_import_for_export"
backend-path = ["."]
""".encode()


def _package(name: str, version: str, mutation: tuple[str, str] | None) -> str:
    wheel_name = name.replace("-", "_")
    sdist_hash = "1" * 64
    wheel_hash = "2" * 64
    wheel_path = "packages"
    if mutation == (name, "sdist-hash"):
        sdist_hash = "3" * 64
    if mutation == (name, "wheel-hash"):
        wheel_hash = "4" * 64
    if mutation == (name, "wheel-url"):
        wheel_path = "different-location"
    dependencies = (
        'dependencies = [{name="hashmarks-child"}]\n' if name == "hashmarks" else ""
    )
    return f'''[[package]]
name = "{name}"
version = "{version}"
source = {{ registry = "https://pypi.org/simple" }}
{dependencies}sdist = {{ url = "https://files.pythonhosted.org/packages/{name}-{version}.tar.gz", hash = "sha256:{sdist_hash}" }}
wheels = [{{ url = "https://files.pythonhosted.org/{wheel_path}/{wheel_name}-{version}-py3-none-any.whl", hash = "sha256:{wheel_hash}" }}]
'''


def _lock(version: str, *, mutation: tuple[str, str] | None = None) -> bytes:
    root = """version = 1
revision = 3
requires-python = ">=3.12"
[[package]]
name = "artifact-fixture"
version = "1"
source = { editable = "." }
dependencies = [{ name = "hashmarks" }, { name = "direct-package" }]
[package.optional-dependencies]
extra = [{ name = "extra-package" }]
[package.dev-dependencies]
quality = [{ name = "quality-package" }]
"""
    return (
        root
        + "".join(
            _package(name, version if name == "hashmarks" else "1", mutation)
            for name in (
                "hashmarks",
                "direct-package",
                "extra-package",
                "quality-package",
                "hashmarks-child",
            )
        )
    ).encode()


def _compare(*, mutation: tuple[str, str] | None = None):
    return owner.compare_native_uv_dependency_artifacts(
        before_manifest=_manifest("1"),
        before_lock=_lock("1"),
        after_manifest=_manifest("2"),
        after_lock=_lock("2", mutation=mutation),
        excluded_packages=("hashmarks",),
    )


def test_native_export_accepts_hashmarks_only_artifact_change() -> None:
    result = _compare(mutation=("hashmarks", "wheel-hash"))

    assert result["equivalent"] is True
    assert result["before_artifact_sha256"] == result["after_artifact_sha256"]
    assert result["before_artifact_bytes"] == result["after_artifact_bytes"]
    assert result["before_artifact_bytes"] > 0
    assert result["schema"] == "oh-goon.native-uv-artifact-comparison/v1"
    assert result["authority"] == "native-uv-export"
    assert result["excluded_packages"] == ["hashmarks"]
    assert result["scope"] == {
        "format": "pylock.toml",
        "groups": "all",
        "extras": "all",
        "workspace": "not-emitted",
        "exclusion": "native-no-emit-package",
    }
    assert result["tool"]["version"].startswith("uv ")
    assert Path(result["tool"]["executable"]).is_absolute()
    # The mutation tests below independently prove that default, optional,
    # development-group and Hashmarks-transitive artifacts were all exported.


@pytest.mark.parametrize("field", ["sdist-hash", "wheel-url", "wheel-hash"])
def test_native_export_observes_unrelated_artifact_only_changes(field: str) -> None:
    result = _compare(mutation=("direct-package", field))
    assert result["equivalent"] is False
    assert result["before_artifact_sha256"] != result["after_artifact_sha256"]


@pytest.mark.parametrize("name", ["extra-package", "quality-package", "hashmarks-child"])
def test_nondefault_and_transitive_artifact_changes_cannot_disappear(name: str) -> None:
    result = _compare(mutation=(name, "wheel-hash"))
    assert result["equivalent"] is False


def test_ambient_project_and_mode_selectors_cannot_redirect_native_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text("not valid TOML\n", encoding="utf-8")
    for name in ("UV_PROJECT", "UV_WORKING_DIR", "UV_CONFIG_FILE"):
        monkeypatch.setenv(name, str(foreign))
    for name in ("UV_NO_PROJECT", "UV_LOCKED", "UV_NO_SYNC"):
        monkeypatch.setenv(name, "1")
    assert _compare()["equivalent"] is True


def test_missing_native_exporter_does_not_admit_empty_equal_inventories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.shutil, "which", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="exporter is unavailable"):
        _compare()


def test_malformed_lock_is_unverified_instead_of_an_empty_export() -> None:
    with pytest.raises(ValueError, match="artifact export failed"):
        owner.compare_native_uv_dependency_artifacts(
            before_manifest=_manifest("1"),
            before_lock=b"not valid TOML",
            after_manifest=_manifest("2"),
            after_lock=_lock("2"),
            excluded_packages=("hashmarks",),
        )


def test_native_export_does_not_interpret_or_accept_empty_input() -> None:
    with pytest.raises(ValueError, match="bounded captured input bytes"):
        owner.compare_native_uv_dependency_artifacts(
            before_manifest=_manifest("1"),
            before_lock=b"",
            after_manifest=_manifest("2"),
            after_lock=_lock("2"),
            excluded_packages=("hashmarks",),
        )
