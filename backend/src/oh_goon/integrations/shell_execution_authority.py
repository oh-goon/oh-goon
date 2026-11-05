"""Execution need from producer source candidates and explicit original argv."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oh_goon.core.source_tree_policy import is_package_manager_materialization_path
from oh_goon.integrations._shell_authority_syntax import (
    CONTROL_WORDS,
    SHELLS,
    external_tool,
    materialized_executable_identity,
    project_path,
)
from oh_goon.integrations.hashmarks import (
    HashmarksSourceEvidence,
    hashmarks_source_scope,
    observe_make_shell_declarations,
    observe_make_shell_declarations_text,
    verify_source_scope,
)
from oh_goon.integrations.package_script_execution_authority import (
    NpmSourceObservations,
    PackageExecutableAuthority,
    derive_npm_run_authority,
    npm_run_script,
)

_FORWARD_DECLARATION = re.compile(r"# OH_GOON_FORWARD_ARGV_FROM: ([1-9][0-9]*)")
_OPAQUE_LAUNCHERS = frozenset(
    {"env", "xargs", "find", "timeout", "nice", "nohup", "stdbuf", "setsid"}
)

_Candidate = tuple[str | None, ...]


@dataclass(slots=True)
class ShellSourceObservations:
    """Captured source facts for one original caller-owned source lifetime."""

    root: Path
    scope: object
    scripts: dict[str, HashmarksSourceEvidence] = field(default_factory=dict)
    npm: NpmSourceObservations = field(init=False)
    work_remaining: int = 4096

    def __post_init__(self) -> None:
        self.npm = NpmSourceObservations(self.root, self.scope)


@dataclass(frozen=True, slots=True)
class ShellAuthority:
    tools: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    scripts: tuple[str, ...]
    package_executables: tuple[PackageExecutableAuthority, ...]
    package_provenance: tuple[tuple[str, str], ...]
    package_sources: tuple[str, ...]
    materialized_executables: tuple[str, ...]
    repository_evidence: tuple[HashmarksSourceEvidence, ...] = ()
    source_identities: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class _Builder:
    root: Path
    scope: object
    working_directory: str = "."
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    context_known: bool = True
    tools: set[str] = field(default_factory=set)
    provenance: set[tuple[str, str]] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    scripts: set[str] = field(default_factory=set)
    package_executables: set[PackageExecutableAuthority] = field(default_factory=set)
    package_provenance: set[tuple[str, str]] = field(default_factory=set)
    package_sources: set[str] = field(default_factory=set)
    materialized_executables: set[str] = field(default_factory=set)
    observations: list[HashmarksSourceEvidence] = field(default_factory=list)
    source_cache: dict[str, HashmarksSourceEvidence] = field(default_factory=dict)
    visiting: list[str] = field(default_factory=list)
    observation_bytes: int = 0
    retained: set[int] = field(default_factory=set)
    npm_observations: NpmSourceObservations | None = None
    source_observations: ShellSourceObservations | None = None
    metadata_bytes: int = 0
    source_identities: dict[str, str] = field(default_factory=dict)

    def require(self, tool: str, chain: tuple[str, ...]) -> None:
        path = " -> ".join((*chain, tool))
        if self._reserve_metadata(path):
            self.tools.add(tool)
            self.provenance.add((tool, path))

    def reject(self, reason: str, chain: tuple[str, ...]) -> None:
        value = " -> ".join(chain) + ": " + reason
        if self._reserve_metadata(value):
            self.unresolved.add(value)

    def _reserve_metadata(self, value: str) -> bool:
        if self.metadata_bytes + len(value.encode("utf-8")) > 4 * 1024 * 1024:
            self.unresolved.add("Execution source metadata transport bound exhausted")
            if self.source_observations is not None:
                self.source_observations.work_remaining = 0
            return False
        self.metadata_bytes += len(value.encode("utf-8"))
        return True

    def visit(self) -> bool:
        if self.source_observations is None or self.source_observations.work_remaining <= 0:
            self.unresolved.add("Execution source observation work bound exhausted")
            return False
        self.source_observations.work_remaining -= 1
        return True

    def retain(self, evidence: HashmarksSourceEvidence) -> None:
        if id(evidence) in self.retained:
            return
        size = len(evidence.canonical_packet_json.encode("utf-8")) + len(
            evidence.source_bytes or b""
        )
        if len(self.observations) >= 128 or self.observation_bytes + size > 4 * 1024 * 1024:
            raise ValueError("Execution source evidence exceeds its bounded transport")
        self.observation_bytes += size
        self.observations.append(evidence)
        self.retained.add(id(evidence))


def _file_observation(evidence: HashmarksSourceEvidence) -> dict[str, Any]:
    packet = evidence.packet()
    providers = packet.get("providers", [])
    groups = packet.get("declarations", {}).get("groups", [])
    if len(providers) != 1 or providers[0].get("state") != "collected" or len(groups) != 1:
        raise ValueError("Execution source observation is unavailable")
    value = groups[0]["declarations"][0]["value"]
    if (
        value.get("facts_completeness") != "complete-supported-subset"
        or value.get("facts_truncation") != "not-truncated"
    ):
        raise ValueError("Execution source observation is incomplete")
    return dict(value)


def _forward_declaration(evidence: HashmarksSourceEvidence) -> int | None:
    if evidence.source_bytes is None:
        raise ValueError("Declared forwarding requires original captured source bytes")
    positions = [
        int(match.group(1))
        for line in evidence.source_bytes.decode("utf-8").split("\n")
        if (match := _FORWARD_DECLARATION.fullmatch(line)) is not None
    ]
    if len(positions) > 1:
        raise ValueError("OH_GOON_FORWARD_ARGV_FROM must be unique")
    return positions[0] if positions else None


def _owned_script(
    builder: _Builder, operand: str, arguments: _Candidate, chain: tuple[str, ...]
) -> None:
    if not builder.visit():
        return
    path = project_path(builder.root, operand, working_directory=builder.working_directory)
    if path is None:
        builder.reject("unproven owned script operand: " + operand, chain)
        return
    relative = path.relative_to(builder.root).as_posix()
    if is_package_manager_materialization_path(Path(relative)):
        builder.materialized_executables.add(relative)
        return
    if relative in builder.visiting:
        builder.reject("owned script cycle: " + " -> ".join((*builder.visiting, relative)), chain)
        return
    evidence = builder.source_cache.get(relative)
    if evidence is None:
        if len(builder.source_cache) >= 128:
            raise ValueError("Owned execution source inventory exceeds its transport bound")
        evidence = observe_make_shell_declarations(
            builder.root,
            shell_paths=(relative,),
            layout="file",
            scope=builder.scope,
            include_source_bytes=True,
        )
        builder.source_cache[relative] = evidence
    builder.retain(evidence)
    if evidence.source_bytes is None:
        raise ValueError("Owned script byte custody is unavailable")
    builder.source_identities[relative] = (
        "sha256:" + hashlib.sha256(evidence.source_bytes).hexdigest()
    )
    builder.scripts.add(relative)
    position = _forward_declaration(evidence)
    if position is not None:
        # The product declaration supplies conservative child input intent.
        # It does not prove that this script actually forwards those operands.
        builder.reject("declared forwarding does not establish native behavior", chain)
        if position <= len(arguments):
            _supplied_argv(
                builder, arguments[position - 1 :], (*chain, relative, "<declared-child-argv>")
            )
        else:
            builder.reject("declared child argv is unavailable", chain)
    builder.visiting.append(relative)
    try:
        _scan_observation(builder, evidence, (*chain, relative))
    finally:
        builder.visiting.pop()


def _npm(builder: _Builder, words: _Candidate, chain: tuple[str, ...]) -> None:
    builder.require("npm", chain)
    known: list[str] = []
    for word in words:
        if word is None:
            break
        known.append(word)
    try:
        script = npm_run_script(tuple(known), 0)
    except ValueError as exc:
        builder.reject(str(exc), chain)
        return
    if script is None:
        return
    if any(marker in script for marker in ("$", "`")):
        builder.reject("dynamic npm script name is unproven", chain)
        return
    authority = derive_npm_run_authority(
        builder.root,
        script,
        chain=(*chain, f"npm run {script}"),
        observations=builder.npm_observations,
        package_dir=builder.root / builder.working_directory if builder.context_known else None,
    )
    builder.tools.update(authority.host_tools)
    builder.provenance.update(authority.host_provenance)
    builder.package_executables.update(authority.package_executables)
    builder.package_provenance.update(authority.package_provenance)
    builder.package_sources.update(authority.sources)
    builder.unresolved.update(authority.unresolved)
    builder.source_identities.update(authority.source_identities)
    for evidence in authority.repository_evidence:
        builder.retain(evidence)


def _positional_command_observed(observed: dict[str, Any]) -> bool:
    for fact in observed["facts"]:
        if fact["kind"] != "shell-command-expression":
            continue
        words = fact["value"].get("words", [])
        operands = words[1:] if words and words[0].get("literal") == "exec" else words[:1]
        if any(word.get("reference", {}).get("name") == "@" for word in operands):
            return True
    return False


def _supplied_argv(builder: _Builder, words: _Candidate, chain: tuple[str, ...]) -> None:
    original_context = builder.context_known
    builder.context_known = False
    try:
        _scan_argv(builder, words, chain, source_command=False)
    finally:
        builder.context_known = original_context


def _shell(builder: _Builder, words: _Candidate, chain: tuple[str, ...]) -> None:
    program = words[0]
    if program is None:
        builder.reject("shell program is unproven", chain)
        return
    builder.require(Path(program).name, chain)
    if len(words) == 1:
        return
    if words[1] == "-c" and len(words) >= 3:
        if words[2] is None:
            builder.reject("shell command source is unproven", chain)
            if len(words) > 4:
                _supplied_argv(builder, words[4:], (*chain, "<conditional-supplied-argv>"))
            return
        evidence = observe_make_shell_declarations_text(
            words[2], path="command.sh", language="shell", layout="file"
        )
        builder.retain(evidence)
        _scan_observation(builder, evidence, (*chain, "<shell-command-source>"))
        if len(words) > 4 and _positional_command_observed(_file_observation(evidence)):
            # $0 remains separate under the native shell ABI. Original supplied
            # operands are conservative intent, never proof that a branch runs.
            _supplied_argv(builder, words[4:], (*chain, "<supplied-shell-argv>"))
        return
    if words[1] is None or words[1].startswith("-"):
        builder.reject("shell option/operand binding is unproven", chain)
        return
    _owned_script(builder, words[1], words[2:], chain)


def _scan_argv(
    builder: _Builder, words: _Candidate, chain: tuple[str, ...], *, source_command: bool = True
) -> None:
    if not builder.visit():
        return
    if not words:
        return
    command = words[0]
    if command is None:
        builder.reject("source command head is unproven", chain)
        return
    if Path(command).is_absolute() and Path(command).name in SHELLS:
        builder.reject("absolute shell requires original admitted native tool role", chain)
        return
    if any(word is None for word in words):
        builder.reject("source argv contains unresolved operands", chain)
    if source_command and command == "exec":
        if (
            len(words) > 1
            and words[1] is not None
            and words[1] != "exec"
            and not words[1].startswith("-")
        ):
            _scan_argv(builder, words[1:], (*chain, "exec"), source_command=False)
        else:
            builder.reject("native exec operand is unproven", chain)
        return
    if source_command and command in {"command", "builtin", "trap"}:
        builder.reject("native builtin operand binding is unproven: " + command, chain)
        return
    if source_command and command in {".", "source", "cd", "eval"}:
        builder.reject("native source/environment change is unproven: " + command, chain)
        return
    if source_command and command in CONTROL_WORDS:
        return
    materialized = materialized_executable_identity(
        command, working_directory=builder.working_directory
    )
    if materialized is not None:
        builder.materialized_executables.add(materialized)
        return
    tool = (
        command
        if not source_command and command in CONTROL_WORDS
        else external_tool(builder.root, command, working_directory=builder.working_directory)
    )
    if tool in SHELLS:
        _shell(builder, words, chain)
    elif tool == "npm":
        _npm(builder, words, chain)
    elif tool is not None:
        builder.require(tool, chain)
        if tool in _OPAQUE_LAUNCHERS:
            builder.reject("native child operand binding is unproven: " + tool, chain)
    elif (
        project_path(builder.root, command, working_directory=builder.working_directory) is not None
    ):
        builder.reject("direct script interpreter requires original execution authority", chain)
        _owned_script(builder, command, words[1:], chain)
    else:
        builder.reject("unproven executable source candidate: " + command, chain)


def _authored_words(value: dict[str, Any]) -> _Candidate:
    """Keep opaque operand slots; never synthesize a runtime expansion."""
    words = value.get("authored_words", value.get("words", []))
    return tuple(word.get("literal") for word in words) if isinstance(words, list) else ()


def _command_candidates(value: dict[str, Any]) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    literal = value.get("literal_argv")
    if isinstance(literal, list):
        candidates.append(tuple(literal))
    else:
        observed = value.get("static_binding_observation", {})
        words = observed.get("words")
        candidates.append(
            tuple(words) if isinstance(words, list) else _authored_words(observed or value)
        )
    for invocation in value.get("invocation_binding_observation", {}).get("candidates", []):
        words = invocation.get("observation", {}).get("words")
        if isinstance(words, list):
            candidates.append(tuple(words))
    return tuple(candidates)


def _scan_observation(
    builder: _Builder,
    evidence: HashmarksSourceEvidence,
    chain: tuple[str, ...],
    indices: Sequence[int] | None = None,
) -> None:
    if not builder.visit():
        return
    observed = _file_observation(evidence)
    facts = observed["facts"]
    selected_indices = tuple(range(len(facts))) if indices is None else tuple(indices)
    original_context = builder.context_known
    if any(
        facts[index]["kind"] != "shell-command-expression"
        or (facts[index]["value"].get("literal_argv") or [None])[0:1]
        in (["cd"], ["source"], ["."], ["eval"])
        or facts[index]["value"].get("command_local_environment")
        for index in selected_indices
    ):
        builder.context_known = False
    try:
        for index in selected_indices:
            if (
                builder.source_observations is None
                or builder.source_observations.work_remaining <= 0
            ):
                builder.unresolved.add("Execution source observation work bound exhausted")
                break
            fact = facts[index]
            value = fact["value"]
            if fact["kind"] != "shell-command-expression":
                if fact["kind"] not in {
                    "shell-function-declaration",
                    "shell-heredoc-data",
                }:
                    builder.reject("unproven authored source syntax: " + fact["kind"], chain)
                continue
            if value.get("command_declaration_candidates"):
                builder.reject("source helper binding is conditional", chain)
                continue
            if value.get("command_local_environment"):
                builder.reject("source environment binding is unproven", chain)
            if (
                value.get("source_language") == "make"
                and (value.get("literal_argv") or [None])[0] in CONTROL_WORDS
            ):
                builder.reject("native Make recipe command binding is unproven", chain)
            if value.get("literal_argv") is None or any(
                not context.startswith("make-recipe:") for context in value.get("context", [])
            ):
                builder.reject("conditional or unresolved source command binding", chain)
            for words in _command_candidates(value):
                if words:
                    _scan_argv(builder, words, chain)
                else:
                    builder.reject("source command head is unproven", chain)
    finally:
        builder.context_known = original_context


def _result(builder: _Builder) -> ShellAuthority:
    return ShellAuthority(
        tools=tuple(sorted(builder.tools)),
        provenance=tuple(sorted(builder.provenance)),
        unresolved=tuple(sorted(builder.unresolved)),
        scripts=tuple(sorted(builder.scripts)),
        package_executables=tuple(sorted(builder.package_executables)),
        package_provenance=tuple(sorted(builder.package_provenance)),
        package_sources=tuple(sorted(builder.package_sources)),
        materialized_executables=tuple(sorted(builder.materialized_executables)),
        repository_evidence=tuple(builder.observations),
        source_identities=tuple(sorted(builder.source_identities.items())),
    )


def derive_shell_observation_authority(
    root: Path,
    evidence: HashmarksSourceEvidence,
    indices: Sequence[int],
    *,
    chain: tuple[str, ...],
    working_directory: str = ".",
    candidate_paths: Mapping[str, Sequence[Sequence[str]]] | None = None,
    source_observations: ShellSourceObservations | None = None,
) -> ShellAuthority:
    root = root.resolve()
    if source_observations is not None and source_observations.root != root:
        raise ValueError("Shell source observations belong to another repository")
    with (
        hashmarks_source_scope(root)
        if source_observations is None
        else nullcontext(source_observations.scope)
    ) as scope:
        captured = source_observations or ShellSourceObservations(root, scope)
        builder = _Builder(
            root=root,
            scope=scope,
            working_directory=working_directory,
            source_cache=captured.scripts,
            npm_observations=captured.npm,
            source_observations=captured,
            context_known=False,
        )
        try:
            verify_source_scope(root, scope)
            builder.retain(evidence)
            if candidate_paths is None:
                _scan_observation(builder, evidence, chain, indices)
            else:
                for index in indices:
                    for path in candidate_paths[str(index)]:
                        _scan_observation(builder, evidence, tuple(path), (index,))
        except (OSError, ValueError) as exc:
            builder.reject(str(exc), chain)
        return _result(builder)


def derive_shell_fragment_authority(
    root: Path,
    fragment: str,
    *,
    chain: tuple[str, ...],
    environment: dict[str, str] | None = None,
    working_directory: str = ".",
    argv: tuple[str, ...] | None = None,
    source_observations: ShellSourceObservations | None = None,
) -> ShellAuthority:
    root = root.resolve()
    if source_observations is not None and source_observations.root != root:
        raise ValueError("Shell source observations belong to another repository")
    with (
        hashmarks_source_scope(root)
        if source_observations is None
        else nullcontext(source_observations.scope)
    ) as scope:
        captured = source_observations or ShellSourceObservations(root, scope)
        builder = _Builder(
            root=root.resolve(),
            scope=scope,
            working_directory=working_directory,
            environment=dict(environment or {}),
            source_cache=captured.scripts,
            npm_observations=captured.npm,
            source_observations=captured,
        )
        try:
            verify_source_scope(root, scope)
            if argv is not None:
                _scan_argv(builder, argv, chain, source_command=False)
            else:
                evidence = observe_make_shell_declarations_text(
                    fragment, path="command.sh", language="shell", layout="file"
                )
                builder.retain(evidence)
                _scan_observation(builder, evidence, chain)
        except (OSError, ValueError) as exc:
            builder.reject(str(exc), chain)
        return _result(builder)
