"""Oh-Goon publication checks for the committed native uv lock.

Native uv remains the sole owner of resolution and freshness semantics. This
module checks only repository-specific publication properties: the committed
lock may reference approved public registries/release artifacts, but it must
not publish host-local or repository-local dependency sources.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

APPROVED_PUBLIC_PYTHON_REGISTRY_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org"})
APPROVED_PUBLIC_PYTHON_ARTIFACT_HOSTS = frozenset({"github.com"})


def _host_absolute_path(value: str) -> bool:
    return bool(
        value.startswith(("/", "~/", "\\"))
        or Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", value)
    )


def _url_portability_issue(value: str) -> str | None:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme == "file":
        return "URL embeds a host-local filesystem source"
    if parsed.username or parsed.password:
        return "URL contains embedded credentials"
    if scheme in {"http", "https"}:
        if scheme == "https" and host in APPROVED_PUBLIC_PYTHON_REGISTRY_HOSTS:
            return None
        if (
            scheme == "https"
            and host in APPROVED_PUBLIC_PYTHON_ARTIFACT_HOSTS
            and "/releases/download/" in parsed.path
        ):
            return None
        return "URL uses a non-public/non-approved Python registry or artifact host"
    return "URL uses a remote source scheme without repository approval"


def _string_portability_issues(value: str, path: str) -> tuple[str, ...]:
    prefix = f"backend/uv.lock {path}: "
    issues: list[str] = []
    if "://" in value:
        issue = _url_portability_issue(value)
        if issue:
            issues.append(prefix + issue)
    if _host_absolute_path(value):
        issues.append(prefix + "contains host-absolute filesystem identity")
    return tuple(issues)


def _visit(value: object, path: str, issues: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            _visit(item, child, issues)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _visit(item, f"{path}[{index}]", issues)
        return
    if isinstance(value, str):
        issues.extend(_string_portability_issues(value, path))


def _local_package_source_issues(payload: dict[str, object]) -> tuple[str, ...]:
    issues: list[str] = []
    packages = payload.get("package", [])
    if not isinstance(packages, list):
        return ()
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            continue
        name = str(package.get("name", f"package[{index}]"))
        source = package.get("source")
        if not isinstance(source, dict):
            continue
        for key in ("path", "directory"):
            if key in source:
                issues.append(
                    "backend/uv.lock "
                    f"package[{index}] {name!r}.source.{key}: "
                    "repository-local dependency sources are not publishable authority"
                )
        editable = source.get("editable")
        if editable is not None and not (name == "oh-goon" and editable == "."):
            issues.append(
                "backend/uv.lock "
                f"package[{index}] {name!r}.source.editable: "
                "only the Oh-Goon project itself may be an editable local source"
            )
    return tuple(issues)


def backend_lock_portability_issues(lock: Path) -> tuple[str, ...]:
    if lock.is_symlink() or not lock.is_file():
        return ("backend/uv.lock must be a regular non-symlink file",)
    try:
        payload = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return (f"backend/uv.lock is unreadable: {exc}",)
    issues: list[str] = []
    _visit(payload, "", issues)
    issues.extend(_local_package_source_issues(payload))
    return tuple(dict.fromkeys(issues))
