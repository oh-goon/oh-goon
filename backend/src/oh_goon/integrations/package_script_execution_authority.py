from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.integrations._shell_authority_syntax import (
    CONTROL_WORDS,
    SIMPLE_TOOL,
    command_index,
    extract_command_substitutions,
    mask_noncommand_expansions,
    segments,
)

_NPM_RUN_SUBCOMMANDS = frozenset({"run", "run-script"})
_NPM_SCRIPT_ALIASES = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "test": "test",
}


@dataclass(frozen=True, slots=True, order=True)
class PackageExecutableAuthority:
    bin_name: str
    package_path: str
    version: str
    bin_target: str
    integrity: str
    package_directory: str = "."

    def identity_row(self) -> tuple[str, str, str, str, str]:
        return (
            self.bin_name,
            self.package_path,
            self.version,
            self.bin_target,
            self.integrity,
        )


@dataclass(frozen=True, slots=True)
class PackageScriptAuthority:
    host_tools: tuple[str, ...]
    host_provenance: tuple[tuple[str, str], ...]
    package_executables: tuple[PackageExecutableAuthority, ...]
    package_provenance: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    sources: tuple[str, ...]
    scripts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PackageContext:
    directory: Path
    package_json: Path
    package_lock: Path
    scripts: dict[str, str]
    bins: dict[str, tuple[PackageExecutableAuthority, ...]]
    direct_packages: frozenset[str]
    bin_providers: dict[str, str]


@dataclass(slots=True)
class _Builder:
    root: Path
    context: _PackageContext
    host_tools: set[str] = field(default_factory=set)
    host_provenance: set[tuple[str, str]] = field(default_factory=set)
    package_executables: set[PackageExecutableAuthority] = field(default_factory=set)
    package_provenance: set[tuple[str, str]] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    scripts: set[str] = field(default_factory=set)
    visiting: list[str] = field(default_factory=list)

    def require_host(self, tool: str, chain: tuple[str, ...]) -> None:
        self.host_tools.add(tool)
        self.host_provenance.add((tool, " -> ".join((*chain, tool))))

    def require_package(
        self, executable: PackageExecutableAuthority, chain: tuple[str, ...]
    ) -> None:
        self.package_executables.add(executable)
        self.package_provenance.add(
            (
                executable.bin_name,
                " -> ".join((*chain, f"package-bin:{executable.bin_name}")),
            )
        )

    def reject(self, message: str, chain: tuple[str, ...]) -> None:
        self.unresolved.add(" -> ".join(chain) + ": " + message)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _package_candidates(root: Path, script: str) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for package_json in root.rglob("package.json"):
        if "node_modules" in package_json.parts:
            continue
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scripts = payload.get("scripts")
        if isinstance(scripts, dict) and isinstance(scripts.get(script), str):
            candidates.append(package_json.parent)
    return tuple(sorted(candidates))


def _top_level_lock_package(path: str) -> bool:
    return path.startswith("node_modules/") and path.count("node_modules/") == 1


def _package_bin_entries(
    package_path: str, raw: object, *, package_directory: str
) -> tuple[PackageExecutableAuthority, ...]:
    if not _top_level_lock_package(package_path) or not isinstance(raw, dict):
        return ()
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        return ()
    bins = raw.get("bin")
    if isinstance(bins, str):
        bins = {package_path.rsplit("/", 1)[-1]: bins}
    if not isinstance(bins, dict):
        return ()
    integrity = raw.get("integrity")
    integrity_value = integrity if isinstance(integrity, str) else ""
    return tuple(
        PackageExecutableAuthority(
            bin_name=bin_name,
            package_path=package_path,
            version=version,
            bin_target=target,
            integrity=integrity_value,
            package_directory=package_directory,
        )
        for bin_name, target in bins.items()
        if isinstance(bin_name, str) and isinstance(target, str)
    )


def _bin_rows(
    lock_payload: dict[str, object], *, package_directory: str
) -> dict[str, tuple[PackageExecutableAuthority, ...]]:
    packages = lock_payload.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("package-lock.json has no packages object")
    index: dict[str, list[PackageExecutableAuthority]] = {}
    for package_path, raw in packages.items():
        if not isinstance(package_path, str):
            continue
        for row in _package_bin_entries(package_path, raw, package_directory=package_directory):
            index.setdefault(row.bin_name, []).append(row)
    return {name: tuple(sorted(rows)) for name, rows in sorted(index.items())}


def _package_name(package_path: str) -> str:
    relative = package_path.removeprefix("node_modules/")
    if relative.startswith("@"):
        parts = relative.split("/", 2)
        return "/".join(parts[:2])
    return relative.split("/", 1)[0]


def _direct_packages(package_payload: dict[str, object]) -> frozenset[str]:
    names: set[str] = set()
    for dependency_field in ("dependencies", "devDependencies", "optionalDependencies"):
        raw = package_payload.get(dependency_field)
        if isinstance(raw, dict):
            names.update(name for name in raw if isinstance(name, str))
    return frozenset(names)


def _bin_provider_contract(package_payload: dict[str, object]) -> dict[str, str]:
    raw = package_payload.get("ohGoonExecutionAuthority")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("ohGoonExecutionAuthority must be an object")
    providers = raw.get("packageBinProviders", {})
    if not isinstance(providers, dict):
        raise ValueError("ohGoonExecutionAuthority.packageBinProviders must be an object")
    result: dict[str, str] = {}
    for bin_name, provider in providers.items():
        if (
            not isinstance(bin_name, str)
            or not bin_name
            or not isinstance(provider, str)
            or not provider
        ):
            raise ValueError(
                "packageBinProviders entries must map non-empty strings to package names"
            )
        result[bin_name] = provider
    return result


def _load_context(root: Path, directory: Path) -> _PackageContext:
    package_json = directory / "package.json"
    package_lock = directory / "package-lock.json"
    if not package_json.is_file():
        raise ValueError(f"package script owner has no package.json: {_relative(root, directory)}")
    if not package_lock.is_file():
        raise ValueError(
            f"package script owner has no package-lock.json: {_relative(root, directory)}"
        )
    try:
        package_payload = json.loads(package_json.read_text(encoding="utf-8"))
        lock_payload = json.loads(package_lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read package-script authority source: {exc}") from exc
    raw_scripts = package_payload.get("scripts")
    if not isinstance(raw_scripts, dict):
        raise ValueError(f"package.json has no scripts object: {_relative(root, package_json)}")
    scripts = {
        name: value
        for name, value in raw_scripts.items()
        if isinstance(name, str) and isinstance(value, str)
    }
    return _PackageContext(
        directory=directory.resolve(),
        package_json=package_json.resolve(),
        package_lock=package_lock.resolve(),
        scripts=scripts,
        bins=_bin_rows(lock_payload, package_directory=_relative(root, directory)),
        direct_packages=_direct_packages(package_payload),
        bin_providers=_bin_provider_contract(package_payload),
    )


def _select_context(root: Path, script: str, package_dir: Path | None) -> _PackageContext:
    if package_dir is not None:
        return _load_context(root, package_dir.resolve())
    candidates = _package_candidates(root, script)
    if not candidates:
        raise ValueError(f"npm script is not owned by a project package: {script}")
    if len(candidates) != 1:
        rendered = ", ".join(_relative(root, path) for path in candidates)
        raise ValueError(f"npm script owner is ambiguous for {script}: {rendered}")
    return _load_context(root, candidates[0])


def npm_run_script(words: tuple[str, ...], index: int) -> str | None:
    """Return a statically named npm script for one npm argv, if present."""

    cursor = index + 1
    while cursor < len(words) and words[cursor].startswith("-"):
        cursor += 1
    if cursor >= len(words):
        return None
    subcommand = words[cursor]
    alias = _NPM_SCRIPT_ALIASES.get(subcommand)
    if alias is not None:
        return alias
    if subcommand not in _NPM_RUN_SUBCOMMANDS:
        return None
    cursor += 1
    while cursor < len(words) and words[cursor].startswith("-"):
        cursor += 1
    if cursor >= len(words) or words[cursor] == "--":
        return None
    return words[cursor]


def _scan_nested_npm(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
) -> None:
    builder.require_host("npm", chain)
    script = npm_run_script(words, index)
    if script is None:
        return
    if "$" in script or "`" in script:
        builder.reject(f"dynamic npm script name: {script}", chain)
        return
    _visit_script(builder, script, (*chain, f"npm run {script}"))


def _scan_package_bin(builder: _Builder, command: str, chain: tuple[str, ...]) -> bool:
    matches = builder.context.bins.get(command, ())
    if not matches:
        return False
    selected: PackageExecutableAuthority | None = None
    if len(matches) == 1:
        selected = matches[0]
    else:
        provider = builder.context.bin_providers.get(command)
        if provider is None:
            packages = ", ".join(row.package_path for row in matches)
            builder.reject(f"ambiguous package-local executable {command}: {packages}", chain)
            return True
        expected_path = f"node_modules/{provider}"
        selected = next((row for row in matches if row.package_path == expected_path), None)
        if selected is None:
            builder.reject(
                f"packageBinProviders selects unavailable provider for {command}: {provider}",
                chain,
            )
            return True
    provider_name = _package_name(selected.package_path)
    if provider_name not in builder.context.direct_packages:
        builder.reject(
            f"package-local executable {command} is provided only transitively by {provider_name}",
            chain,
        )
        return True
    builder.require_package(selected, chain)
    return True


def _scan_special_command(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    command: str,
    chain: tuple[str, ...],
) -> bool:
    if command.startswith(("$", "${")):
        builder.reject(f"unresolved package-script executable variable: {command}", chain)
        return True
    if command == "npm":
        _scan_nested_npm(builder, words, index, chain)
        return True
    if command == "npx":
        builder.reject(
            "npx execution is not package-script authority; use a locked npm script", chain
        )
        return True
    return _scan_package_bin(builder, command, chain)


def _scan_host_command(builder: _Builder, command: str, chain: tuple[str, ...]) -> None:
    if "/" in command:
        builder.reject(f"unresolved package-script executable path: {command}", chain)
    elif SIMPLE_TOOL.fullmatch(command) is None:
        builder.reject(f"unresolved package-script executable token: {command!r}", chain)
    else:
        builder.require_host(command, chain)


def _scan_words(builder: _Builder, words: tuple[str, ...], chain: tuple[str, ...]) -> None:
    if not words:
        return
    index = command_index(words)
    if index >= len(words):
        return
    command = words[index].lstrip("({").rstrip(")}")
    if not command or command in CONTROL_WORDS or command.endswith(")"):
        return
    if _scan_special_command(builder, words, index, command, chain):
        return
    _scan_host_command(builder, command, chain)


def _scan_script_body(builder: _Builder, script: str, body: str, chain: tuple[str, ...]) -> None:
    cleaned, substitutions = extract_command_substitutions(body)
    for substitution in substitutions:
        _scan_script_body(builder, script, substitution, (*chain, "<command-substitution>"))
    cleaned = mask_noncommand_expansions(cleaned)
    parsed = segments(cleaned)
    if parsed is None:
        builder.reject(f"unparseable npm script body: {body}", chain)
        return
    for words in parsed:
        _scan_words(builder, words, chain)


def _visit_script(builder: _Builder, script: str, chain: tuple[str, ...]) -> None:
    if script in builder.visiting:
        builder.reject("npm script cycle: " + " -> ".join((*builder.visiting, script)), chain)
        return
    body = builder.context.scripts.get(script)
    if body is None:
        builder.reject(f"npm script is missing from package.json: {script}", chain)
        return
    builder.scripts.add(script)
    builder.require_host("sh", (*chain, "<npm-script-shell>"))
    builder.visiting.append(script)
    try:
        _scan_script_body(builder, script, body, chain)
    finally:
        builder.visiting.pop()


def derive_npm_run_authority(
    root: Path,
    script: str,
    *,
    chain: tuple[str, ...],
    package_dir: Path | None = None,
) -> PackageScriptAuthority:
    """Prove one project-owned npm script and its locked package-local executable closure."""

    root = root.resolve()
    try:
        context = _select_context(root, script, package_dir)
    except ValueError as exc:
        return PackageScriptAuthority(
            (), (), (), (), (" -> ".join(chain) + ": " + str(exc),), (), ()
        )
    builder = _Builder(root=root, context=context)
    _visit_script(builder, script, chain)
    return PackageScriptAuthority(
        host_tools=tuple(sorted(builder.host_tools)),
        host_provenance=tuple(sorted(builder.host_provenance)),
        package_executables=tuple(sorted(builder.package_executables)),
        package_provenance=tuple(sorted(builder.package_provenance)),
        unresolved=tuple(sorted(builder.unresolved)),
        sources=(
            _relative(root, context.package_json),
            _relative(root, context.package_lock),
        ),
        scripts=tuple(sorted(builder.scripts)),
    )
