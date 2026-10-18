from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[4]
Runner = Callable[..., subprocess.CompletedProcess[str]]


def backend_requirements(root: Path = ROOT) -> tuple[str, str]:
    backend = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    python_spec = backend["project"]["requires-python"]
    uv_spec = backend["tool"]["uv"]["required-version"]
    if not isinstance(python_spec, str) or not isinstance(uv_spec, str):
        raise ValueError("backend native-tool requirements must be strings")
    return python_spec, uv_spec


def frontend_requirements(root: Path = ROOT) -> tuple[str, str]:
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    engines = frontend["engines"]
    node_spec = engines["node"]
    npm_spec = engines["npm"]
    if not isinstance(node_spec, str) or not isinstance(npm_spec, str):
        raise ValueError("frontend native-tool requirements must be strings")
    return node_spec, npm_spec


def requirements(root: Path = ROOT) -> tuple[str, str, str, str]:
    python_spec, uv_spec = backend_requirements(root)
    node_spec, npm_spec = frontend_requirements(root)
    return python_spec, uv_spec, node_spec, npm_spec


def _tool_version(name: str, runner: Runner) -> tuple[str | None, str | None]:
    try:
        result = runner((name, "--version"), cwd=ROOT, check=True, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    rendered = (result.stdout or result.stderr).strip()
    if not rendered:
        return None, f"{name} --version produced no output"
    return rendered, None


def startup_report(
    root: Path = ROOT,
    *,
    runner: Runner = subprocess.run,
    resolved_python_version: str | None = None,
    components: tuple[str, ...] | None = None,
) -> tuple[tuple[str, str, bool, str], ...]:
    """Report native startup tools without reimplementing their version semantics.

    Native uv owns ``requires-python`` and ``[tool.uv].required-version``. Native npm,
    with ``frontend/.npmrc`` ``engine-strict=true``, owns the package ``engines``
    contract. This diagnostic reports the observed tools and owning declarations; the
    native materialization commands remain the compatibility authority.
    """

    requested = set(components or ("Python", "uv", "node", "npm"))
    unknown = requested - {"Python", "uv", "node", "npm"}
    if unknown:
        raise ValueError(f"unknown startup component(s): {', '.join(sorted(unknown))}")

    backend_requested = bool(requested & {"Python", "uv"})
    frontend_requested = bool(requested & {"node", "npm"})
    python_spec = uv_spec = node_spec = npm_spec = ""
    if backend_requested:
        python_spec, uv_spec = backend_requirements(root)
    if frontend_requested:
        node_spec, npm_spec = frontend_requirements(root)

    rows: list[tuple[str, str, bool, str]] = []
    if "Python" in requested:
        rows.append(
            (
                "Python",
                resolved_python_version or "uv-managed",
                True,
                f"native uv owns requires-python {python_spec}",
            )
        )

    native_requirements = (
        ("uv", uv_spec, "backend/pyproject.toml [tool.uv].required-version"),
        ("node", node_spec, "frontend/package.json engines.node"),
        ("npm", npm_spec, "frontend/package.json engines.npm"),
    )
    for name, spec, owner in native_requirements:
        if name not in requested:
            continue
        version, error = _tool_version(name, runner)
        if error is not None or version is None:
            rows.append((name, "missing", False, error or "missing"))
            continue
        rows.append((name, version, True, f"native tool owns {owner} {spec}"))
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:] or ["check"]
    mode = args[0]
    if mode not in {"check", "doctor"}:
        print(
            "usage: oh-goon-startup-environment [check|doctor] [Python|uv|node|npm ...]",
            file=sys.stderr,
        )
        return 2
    components = tuple(args[1:]) or None
    try:
        rows = startup_report(ROOT, components=components)
    except (
        OSError,
        KeyError,
        ValueError,
        tomllib.TOMLDecodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Oh Goon environment contract is invalid: {exc}", file=sys.stderr)
        return 1
    ok = True
    if mode == "doctor":
        print("Oh Goon environment")
    for name, version, passed, detail in rows:
        ok &= passed
        if mode == "doctor":
            print(f"{name:<8} {version:<16} {'OK' if passed else 'FAIL'} — {detail}")
        elif not passed:
            print(f"{name} check failed: {version} — {detail}", file=sys.stderr)
    return 0 if ok else 1
