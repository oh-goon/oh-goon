from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path, PureWindowsPath
import tomllib
from urllib.parse import urlsplit

from oh_goon.repository_tooling._dependency_lock_portability import backend_lock_portability_issues

ROOT = Path(__file__).resolve().parents[4]
PUBLIC_NPM_REGISTRY_HOSTS = frozenset({"registry.npmjs.org"})
_SENSITIVE_ENV = (
    "UV_DEFAULT_INDEX",
    "UV_INDEX",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "NPM_CONFIG_REGISTRY",
    "OH_GOON_PRIVATE_INFRA_HOSTS",
)
_URL_RE = re.compile(r"(?:https?|git\+https?|ssh|file)://[^\s\"'<>]+", re.IGNORECASE)


def _hostname_private(host: str | None) -> bool:
    if not host:
        return False
    value = host.strip("[]").lower().rstrip(".")
    if value in {"localhost", "localhost.localdomain"}:
        return True
    if value.endswith((".internal", ".local", ".corp", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def _configured_private_hosts(environment: dict[str, str] | None = None) -> set[str]:
    env = os.environ if environment is None else environment
    hosts: set[str] = set()
    explicit = env.get("OH_GOON_PRIVATE_INFRA_HOSTS", "")
    hosts.update(item.strip().lower().rstrip(".") for item in explicit.split(",") if item.strip())
    for key in _SENSITIVE_ENV[:-1]:
        value = env.get(key, "")
        for token in re.split(r"[\s,]+", value):
            if "://" not in token:
                continue
            host = urlsplit(token).hostname
            if host:
                hosts.add(host.lower().rstrip("."))
    return hosts


def _url_issue(url: str, *, allow_public_npm: bool = False, configured: set[str] | None = None) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() == "file":
        return "URL embeds a host-local filesystem source"
    if parsed.username or parsed.password:
        return "URL contains embedded credentials"
    if _hostname_private(host) or host in (configured or set()):
        return "URL discloses operator/private infrastructure identity"
    if allow_public_npm and host not in PUBLIC_NPM_REGISTRY_HOSTS:
        return "npm resolved URL uses a non-public/non-approved registry host"
    return None


def frontend_package_lock_privacy_issues(root: Path = ROOT, *, environment: dict[str, str] | None = None) -> tuple[str, ...]:
    lock = root / "frontend/package-lock.json"
    if not lock.exists():
        return ()
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (f"frontend/package-lock.json is unreadable: {exc}",)
    configured = _configured_private_hosts(environment)
    issues: list[str] = []
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        return ("frontend/package-lock.json must contain an npm packages object",)
    for key, value in packages.items():
        if not isinstance(value, dict):
            continue
        resolved = value.get("resolved")
        if not isinstance(resolved, str) or "://" not in resolved:
            continue
        issue = _url_issue(resolved, allow_public_npm=True, configured=configured)
        if issue:
            issues.append(f"frontend/package-lock.json package {key or '<root>'}: {issue}")
    return tuple(issues)



def backend_uv_lock_privacy_issues(
    root: Path = ROOT, *, environment: dict[str, str] | None = None
) -> tuple[str, ...]:
    del environment  # backend lock publication policy is repository-owned, not ambient-configured.
    return backend_lock_portability_issues(root / "backend/uv.lock")


def public_source_privacy_issues(root: Path = ROOT, *, environment: dict[str, str] | None = None) -> tuple[str, ...]:
    configured = _configured_private_hosts(environment)
    issues = list(frontend_package_lock_privacy_issues(root, environment=environment))
    issues.extend(backend_uv_lock_privacy_issues(root, environment=environment))
    candidates = (
        root / "backend/pyproject.toml",
        root / "frontend/package.json",
        root / "backend/uv.lock",
    )
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _URL_RE.finditer(text):
            issue = _url_issue(match.group(0), configured=configured)
            if issue:
                issues.append(f"{path.relative_to(root).as_posix()}: {issue}")
        lowered = text.lower()
        if any(host and host in lowered for host in configured):
            issues.append(f"{path.relative_to(root).as_posix()}: contains configured private infrastructure hostname")
    return tuple(dict.fromkeys(issues))


def assert_public_source_privacy(root: Path = ROOT) -> None:
    issues = public_source_privacy_issues(root)
    if issues:
        raise ValueError("public dependency privacy check failed: " + "; ".join(issues))
