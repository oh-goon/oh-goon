from __future__ import annotations

import hashlib
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oh_goon.integrations._shell_authority_syntax import CONTROL_WORDS, SIMPLE_TOOL
from oh_goon.integrations.hashmarks import (
    HashmarksSourceEvidence,
    capture_source_member,
    hashmarks_source_scope,
    observe_make_shell_declarations_text,
    observe_npm_project_declarations,
    verify_source_scope,
)

_NPM_RUN_SUBCOMMANDS = frozenset({"run", "run-script"})
_NPM_SCRIPT_ALIASES = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "test": "test",
}
_NPM_SOURCE_FLAGS = frozenset({"--silent", "--quiet", "--verbose", "-s", "-q"})
_NPM_NATIVE_OPERATIONS = frozenset({"ls", "list", "help", "--help", "--version", "-v"})


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
    repository_evidence: tuple[HashmarksSourceEvidence, ...] = ()
    source_identities: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _PackageContext:
    directory: Path
    package_json: Path
    package_lock: Path
    scripts: dict[str, str]
    bins: dict[str, tuple[PackageExecutableAuthority, ...]]
    direct_packages: frozenset[str]
    bin_providers: dict[str, str]
    evidence: tuple[HashmarksSourceEvidence, ...]
    source_identities: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class NpmSourceObservations:
    """Captured npm inputs reused only within one caller-owned source lifetime."""

    root: Path
    scope: object
    contexts: dict[Path, _PackageContext] = field(default_factory=dict)
    discovery: tuple[HashmarksSourceEvidence, dict[str, dict[str, Any]]] | None = None
    scripts: dict[tuple[Path, str], HashmarksSourceEvidence] = field(default_factory=dict)
    work_remaining: int = 4096


@dataclass(slots=True)
class _Builder:
    root: Path
    context: _PackageContext
    source_observations: NpmSourceObservations
    host_tools: set[str] = field(default_factory=set)
    host_provenance: set[tuple[str, str]] = field(default_factory=set)
    package_executables: set[PackageExecutableAuthority] = field(default_factory=set)
    package_provenance: set[tuple[str, str]] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    scripts: set[str] = field(default_factory=set)
    visiting: list[str] = field(default_factory=list)
    observations: list[HashmarksSourceEvidence] = field(default_factory=list)
    parsed_scripts: dict[tuple[Path, str], HashmarksSourceEvidence] = field(default_factory=dict)
    metadata_bytes: int = 0

    def _reserve_metadata(self, value: str) -> bool:
        if self.metadata_bytes + len(value.encode("utf-8")) > 4 * 1024 * 1024:
            self.unresolved.add("Npm source metadata transport bound exhausted")
            self.source_observations.work_remaining = 0
            return False
        self.metadata_bytes += len(value.encode("utf-8"))
        return True

    def require_host(self, tool: str, chain: tuple[str, ...]) -> None:
        path = " -> ".join((*chain, tool))
        if self._reserve_metadata(path):
            self.host_tools.add(tool)
            self.host_provenance.add((tool, path))

    def require_package(
        self, executable: PackageExecutableAuthority, chain: tuple[str, ...]
    ) -> None:
        path = " -> ".join((*chain, f"package-bin:{executable.bin_name}"))
        if self._reserve_metadata(path):
            self.package_executables.add(executable)
            self.package_provenance.add((executable.bin_name, path))

    def reject(self, message: str, chain: tuple[str, ...]) -> None:
        value = " -> ".join(chain) + ": " + message
        if self._reserve_metadata(value):
            self.unresolved.add(value)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _npm_values(
    root: Path, paths: tuple[str, ...] | None, *, scope: object | None = None
) -> tuple[HashmarksSourceEvidence, dict[str, dict[str, Any]]]:
    evidence = observe_npm_project_declarations(
        root, paths, fields=("ohGoonExecutionAuthority",), scope=scope
    )
    packet = evidence.packet()
    providers = packet.get("providers", [])
    if (
        len(providers) != 1
        or providers[0].get("state") != "collected"
        or packet.get("freshness") == "stale"
    ):
        raise ValueError("Npm source declaration evidence is unavailable or stale")
    return evidence, {
        group["scope"]["manifest_path"]: group["declarations"][0]["value"]
        for group in packet["declarations"]["groups"]
    }


def _package_candidates(
    root: Path,
    script: str,
    *,
    scope: object | None = None,
    declarations: dict[str, dict[str, Any]] | None = None,
) -> tuple[Path, ...]:
    if declarations is None:
        _evidence, declarations = _npm_values(root, None, scope=scope)
    return tuple(
        sorted(
            (root / path).parent
            for path, value in declarations.items()
            if script in value["scripts"]
        )
    )


def _bin_rows(
    lock: dict[str, Any], *, package_directory: str
) -> dict[str, tuple[PackageExecutableAuthority, ...]]:
    """Project explicit object-form bin declarations into execution candidates.

    Native string-bin naming is not established by source evidence. Nested slots
    are outside this consumer's direct-provider eligibility policy.
    """
    index: dict[str, list[PackageExecutableAuthority]] = {}
    for row in lock["bin_declarations"]:
        location = row["package_location"]
        if not location.startswith("node_modules/") or location.count("node_modules/") != 1:
            continue
        if not row.get("version") or not isinstance(row["bin"], dict):
            raise ValueError("Package bin declaration has no explicit name/version authority")
        for name, target in row["bin"].items():
            index.setdefault(name, []).append(
                PackageExecutableAuthority(
                    bin_name=name,
                    package_path=location,
                    version=row["version"],
                    bin_target=target,
                    integrity=row.get("integrity", ""),
                    package_directory=package_directory,
                )
            )
    return {name: tuple(sorted(rows)) for name, rows in sorted(index.items())}


def _bin_provider_contract(selected_fields: dict[str, Any]) -> dict[str, str]:
    """Validate Oh-Goon's own selected input policy, without npm inference."""
    raw = selected_fields.get("ohGoonExecutionAuthority")
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


def _load_context(
    root: Path,
    directory: Path,
    *,
    scope: object | None = None,
    captured_manifest: tuple[HashmarksSourceEvidence, dict[str, Any]] | None = None,
) -> _PackageContext:
    package_json = directory / "package.json"
    package_lock = directory / "package-lock.json"
    paths = (
        (_relative(root, package_lock),)
        if captured_manifest is not None
        else (_relative(root, package_json), _relative(root, package_lock))
    )
    evidence, declarations = _npm_values(root, paths, scope=scope)
    manifest = (
        captured_manifest[1]
        if captured_manifest is not None
        else declarations.get(_relative(root, package_json))
    )
    lock = declarations.get(_relative(root, package_lock))
    if manifest is None or lock is None:
        missing = "package.json" if manifest is None else "package-lock.json"
        raise ValueError(f"package script owner has no {missing}: {_relative(root, directory)}")
    if "scripts" not in manifest["declared_fields"]:
        raise ValueError(f"package.json has no scripts object: {_relative(root, package_json)}")
    observations = (
        (captured_manifest[0], evidence) if captured_manifest is not None else (evidence,)
    )
    custody = _source_custody(
        root,
        (_relative(root, package_json), _relative(root, package_lock)),
        observations,
        scope=scope,
    )
    return _PackageContext(
        directory=directory.resolve(),
        package_json=package_json.resolve(),
        package_lock=package_lock.resolve(),
        scripts=dict(manifest["scripts"]),
        bins=_bin_rows(lock, package_directory=_relative(root, directory)),
        direct_packages=frozenset(
            name for entries in manifest["dependency_declarations"].values() for name in entries
        ),
        bin_providers=_bin_provider_contract(manifest["selected_fields"]),
        evidence=observations,
        source_identities=custody,
    )


def _source_custody(
    root: Path,
    paths: tuple[str, ...],
    observations: tuple[HashmarksSourceEvidence, ...],
    *,
    scope: object | None,
) -> tuple[tuple[str, str], ...]:
    expected = {
        row["path"]: (evidence.producer_implementation_identity, row["member_revision"])
        for evidence in observations
        for provider in evidence.packet()["providers"]
        for row in provider["inputs"]
        if row.get("observation_kind") == "content" and row["path"] in paths
    }
    result: list[tuple[str, str]] = []
    for path in paths:
        capture = capture_source_member(root, path, scope=scope, max_bytes=4 * 1024 * 1024)
        if capture.source_bytes is None or expected.get(path) != (
            capture.producer_implementation_identity,
            capture.packet().get("member_revision"),
        ):
            raise ValueError("Npm source bytes differ from the original producer observation")
        # This is execution byte custody, never a producer identity/freshness claim.
        result.append((path, "sha256:" + hashlib.sha256(capture.source_bytes).hexdigest()))
    return tuple(sorted(result))


def _select_context(
    root: Path,
    script: str,
    package_dir: Path | None,
    *,
    scope: object | None = None,
    observations: NpmSourceObservations | None = None,
) -> _PackageContext:
    if observations is not None and observations.root != root:
        raise ValueError("Npm source observations belong to another repository")
    if package_dir is not None:
        directory = package_dir.resolve()
        if observations is not None and directory in observations.contexts:
            return observations.contexts[directory]
        if observations is not None and len(observations.contexts) >= 16:
            raise ValueError("Npm source context inventory exceeds its observation bound")
        context = _load_context(root, directory, scope=scope)
        if observations is not None:
            observations.contexts[directory] = context
        return context
    if observations is not None and observations.discovery is not None:
        manifest_evidence, declarations = observations.discovery
    else:
        manifest_evidence, declarations = _npm_values(root, None, scope=scope)
        if observations is not None:
            observations.discovery = manifest_evidence, declarations
    candidates = _package_candidates(root, script, declarations=declarations)
    if not candidates:
        raise ValueError(f"npm source context is unproven for {script}: no qualified candidate")
    if len(candidates) != 1:
        rendered = ", ".join(_relative(root, path) for path in candidates)
        raise ValueError(f"npm script owner is ambiguous for {script}: {rendered}")
    if observations is not None and candidates[0] in observations.contexts:
        return observations.contexts[candidates[0]]
    if observations is not None and len(observations.contexts) >= 16:
        raise ValueError("Npm source context inventory exceeds its observation bound")
    context = _load_context(
        root,
        candidates[0],
        scope=scope,
        captured_manifest=(
            manifest_evidence,
            declarations[_relative(root, candidates[0] / "package.json")],
        ),
    )
    if observations is not None:
        observations.contexts[candidates[0]] = context
    return context


def npm_run_script(words: tuple[str, ...], index: int) -> str | None:
    """Extract only supported explicit script intent; never guess option operands."""

    cursor = index + 1
    while cursor < len(words) and words[cursor] in _NPM_SOURCE_FLAGS:
        cursor += 1
    if cursor >= len(words):
        return None
    subcommand = words[cursor]
    if subcommand in _NPM_NATIVE_OPERATIONS:
        return None
    alias = _NPM_SCRIPT_ALIASES.get(subcommand)
    if alias is not None:
        return alias
    if subcommand not in _NPM_RUN_SUBCOMMANDS:
        raise ValueError(f"npm native operation or command binding is unproven: {subcommand}")
    cursor += 1
    while cursor < len(words) and words[cursor] in _NPM_SOURCE_FLAGS:
        cursor += 1
    if cursor < len(words) and words[cursor] == "--":
        cursor += 1
    if cursor >= len(words) or words[cursor].startswith("-"):
        raise ValueError("npm script name or option binding is unproven")
    return words[cursor]


def _scan_nested_npm(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
) -> None:
    builder.require_host("npm", chain)
    try:
        script = npm_run_script(words, index)
    except ValueError as exc:
        builder.reject(str(exc), chain)
        return
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
    if not any(
        selected.package_path == f"node_modules/{name}" for name in builder.context.direct_packages
    ):
        builder.reject(
            f"package-local executable {command} is provided only transitively by {selected.package_path}",
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
    index = 0
    command = words[index]
    if command in {"exec", "command", "builtin", "trap", "eval", ".", "source", "cd"}:
        builder.reject("native builtin operand binding is unproven: " + command, chain)
        return
    if not command or command in CONTROL_WORDS:
        return
    if _scan_special_command(builder, words, index, command, chain):
        return
    _scan_host_command(builder, command, chain)
    if command in {
        "sh",
        "bash",
        "dash",
        "env",
        "find",
        "xargs",
        "timeout",
        "nice",
        "nohup",
        "stdbuf",
        "setsid",
    }:
        builder.reject("native child operand binding is unproven: " + command, chain)


def _scan_script_body(builder: _Builder, script: str, body: str, chain: tuple[str, ...]) -> None:
    key = builder.context.directory, script
    evidence = builder.parsed_scripts.get(key)
    if evidence is None:
        try:
            evidence = observe_make_shell_declarations_text(
                body, path="npm-script.sh", language="shell", layout="file"
            )
        except (OSError, ValueError) as exc:
            builder.reject(f"npm script source observation is unavailable: {exc}", chain)
            return
        if (
            len(builder.parsed_scripts) >= 128
            or sum(
                len(item.canonical_packet_json.encode("utf-8"))
                for item in builder.parsed_scripts.values()
            )
            + len(evidence.canonical_packet_json.encode("utf-8"))
            > 4 * 1024 * 1024
        ):
            builder.reject("Npm script source evidence exceeds its transport bound", chain)
            return
        builder.parsed_scripts[key] = evidence
    if evidence not in builder.observations:
        builder.observations.append(evidence)
    packet = evidence.packet()
    providers = packet.get("providers", [])
    groups = packet.get("declarations", {}).get("groups", [])
    if len(providers) != 1 or providers[0].get("state") != "collected" or len(groups) != 1:
        builder.reject("npm script source observation is unavailable", chain)
        return
    observed = groups[0]["declarations"][0]["value"]
    if (
        observed.get("facts_completeness") != "complete-supported-subset"
        or observed.get("facts_truncation") != "not-truncated"
    ):
        builder.reject("npm script source observation is incomplete", chain)
        return
    for fact in observed["facts"]:
        value = fact["value"]
        if fact["kind"] == "shell-command-expression":
            if value.get("command_local_environment"):
                builder.reject("npm script source environment binding is unproven", chain)
            words = value.get("literal_argv")
            if not isinstance(words, list):
                builder.reject("npm script command words are unproven", chain)
                continue
            # This is a conservative union of producer-observed source
            # candidates. No branch is selected and no native expansion runs.
            _scan_words(builder, tuple(words), chain)
        elif fact["kind"] != "shell-function-declaration":
            builder.reject("unproven npm script source syntax", chain)


def _visit_script(builder: _Builder, script: str, chain: tuple[str, ...]) -> None:
    if builder.source_observations.work_remaining <= 0:
        builder.unresolved.add("Npm source observation work bound exhausted")
        return
    builder.source_observations.work_remaining -= 1
    if script in builder.visiting:
        builder.reject("npm script cycle: " + " -> ".join((*builder.visiting, script)), chain)
        return
    body = builder.context.scripts.get(script)
    if body is None:
        builder.reject(f"npm script is missing from package.json: {script}", chain)
        return
    if any(prefix + script in builder.context.scripts for prefix in ("pre", "post")):
        builder.reject("native npm lifecycle script binding is unproven", chain)
    builder.scripts.add(script)
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
    observations: NpmSourceObservations | None = None,
) -> PackageScriptAuthority:
    """Prove one project-owned npm script and its locked package-local executable closure."""

    root = root.resolve()
    try:
        with (
            hashmarks_source_scope(root)
            if observations is None
            else nullcontext(observations.scope)
        ) as scope:
            verify_source_scope(root, scope)
            captured = observations or NpmSourceObservations(root, scope)
            context = _select_context(root, script, package_dir, scope=scope, observations=captured)
            builder = _Builder(
                root=root,
                context=context,
                source_observations=captured,
                observations=list(context.evidence),
                parsed_scripts=captured.scripts,
            )
            if package_dir is None:
                builder.reject("native npm working directory/context is unproven", chain)
            builder.reject("native npm script-shell configuration is unproven", chain)
            _visit_script(builder, script, chain)
    except ValueError as exc:
        return PackageScriptAuthority(
            (), (), (), (), (" -> ".join(chain) + ": " + str(exc),), (), ()
        )
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
        repository_evidence=tuple(builder.observations),
        source_identities=context.source_identities,
    )
