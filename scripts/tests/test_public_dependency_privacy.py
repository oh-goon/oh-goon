from __future__ import annotations

import json
from pathlib import Path

from oh_goon.repository_tooling.public_dependency_privacy import (
    backend_uv_lock_privacy_issues,
    frontend_package_lock_privacy_issues,
    public_source_privacy_issues,
)


def _root(tmp_path: Path, resolved: str) -> Path:
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "backend/pyproject.toml").write_text("[project]\nname='x'\nversion='1.0'\n", encoding="utf-8")
    (root / "frontend/package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (root / "backend/uv.lock").write_text('version = 1\nrequires-python = ">=3.12"\npackage = []\n', encoding="utf-8")
    (root / "frontend/package-lock.json").write_text(json.dumps({
        "lockfileVersion": 3,
        "packages": {"": {"name": "x"}, "node_modules/a": {"version": "1.0.0", "resolved": resolved, "integrity": "sha512-x"}},
    }), encoding="utf-8")
    return root


def test_public_npm_registry_is_allowed(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    assert frontend_package_lock_privacy_issues(root) == ()


def test_private_npm_registry_is_rejected_without_echoing_hostname(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://npm.company.internal/a/-/a-1.0.0.tgz")
    issues = frontend_package_lock_privacy_issues(root)
    assert issues
    assert "npm.company.internal" not in " ".join(issues)


def test_operator_configured_host_is_rejected_from_public_intent(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/pyproject.toml").write_text("[project]\nname='x'\nurl='https://packages.example.com/simple'\n", encoding="utf-8")
    issues = public_source_privacy_issues(root, environment={"UV_DEFAULT_INDEX": "https://packages.example.com/simple"})
    assert issues
    assert "packages.example.com" not in " ".join(issues)


def test_private_backend_lock_registry_is_rejected_without_echoing_hostname(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "a"\nversion = "1.0.0"\nsource = { registry = "https://python.company.internal/simple" }\n',
        encoding="utf-8",
    )
    issues = public_source_privacy_issues(root)
    assert issues
    assert "python.company.internal" not in " ".join(issues)


def test_backend_lock_rejects_file_url_local_source(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "local"\nversion = "1.0.0"\nsource = { url = "file:///home/user/private/local.whl" }\n',
        encoding="utf-8",
    )
    issues = backend_uv_lock_privacy_issues(root)
    assert any("host-local filesystem source" in issue for issue in issues)
    assert "/home/user/private" not in " ".join(issues)


def test_backend_lock_rejects_windows_absolute_local_source(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "local"\nversion = "1.0.0"\nsource = { path = "C:/Users/builder/local.whl" }\n',
        encoding="utf-8",
    )
    issues = backend_uv_lock_privacy_issues(root)
    assert any("host-absolute filesystem identity" in issue for issue in issues)


def test_backend_lock_allows_https_github_release_artifact(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "hashmarks"\nversion = "0.25.1"\n'
        'source = { url = "https://github.com/Hugloss/Hashmarks/releases/download/v0.25.1/hashmarks-0.25.1-py3-none-any.whl" }\n'
        'wheels = [{ url = "https://github.com/Hugloss/Hashmarks/releases/download/v0.25.1/hashmarks-0.25.1-py3-none-any.whl", hash = "sha256:abc" }]\n',
        encoding="utf-8",
    )
    assert backend_uv_lock_privacy_issues(root) == ()


def test_backend_lock_rejects_arbitrary_github_url(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "a"\nversion = "1.0.0"\n'
        'source = { url = "https://github.com/Hugloss/Hashmarks/archive/refs/heads/main.zip" }\n',
        encoding="utf-8",
    )
    issues = backend_uv_lock_privacy_issues(root)
    assert any("non-public/non-approved Python registry or artifact host" in issue for issue in issues)


def test_backend_lock_rejects_http_github_release_artifact(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "a"\nversion = "1.0.0"\n'
        'source = { url = "http://github.com/Hugloss/Hashmarks/releases/download/v0.25.1/hashmarks-0.25.1-py3-none-any.whl" }\n',
        encoding="utf-8",
    )
    issues = backend_uv_lock_privacy_issues(root)
    assert any("non-public/non-approved Python registry or artifact host" in issue for issue in issues)


def test_backend_lock_allows_repository_approved_public_python_hosts(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "a"\nversion = "1.0.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        'sdist = { url = "https://files.pythonhosted.org/packages/a/a-1.0.0.tar.gz", hash = "sha256:abc" }\n',
        encoding="utf-8",
    )
    assert backend_uv_lock_privacy_issues(root) == ()


def test_backend_lock_rejects_nonpublic_registry_even_without_ambient_hint(tmp_path: Path) -> None:
    root = _root(tmp_path, "https://registry.npmjs.org/a/-/a-1.0.0.tgz")
    private_host = "repo.intern.example.test"
    (root / "backend/uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n[[package]]\nname = "a"\nversion = "1.0.0"\n'
        f'source = {{ registry = "https://{private_host}/simple" }}\n',
        encoding="utf-8",
    )
    issues = backend_uv_lock_privacy_issues(root)
    assert any("non-public/non-approved Python registry" in issue for issue in issues)
    assert private_host not in " ".join(issues)
