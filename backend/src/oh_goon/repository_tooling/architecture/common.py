from __future__ import annotations

import ast
import os
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oh_goon.integrations.hashmarks import (
    HashmarksSourceEvidence,
    hashmarks_source_scope,
    observe_python_project_declarations_text,
    observe_source_bindings,
)

if TYPE_CHECKING:
    from oh_goon.repository_tooling.architecture.typescript_semantics import TypeScriptFacts


def project_scripts_from_text(text: str) -> dict[str, str]:
    """Project producer-declared script bindings; installed launchers stay unproven."""
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    evidence = snapshot.manifest_evidence.get(text) if snapshot is not None else None
    if evidence is None:
        evidence = observe_python_project_declarations_text(text)
        if snapshot is not None:
            snapshot.manifest_evidence[text] = evidence
    scripts: dict[str, str] = {}
    for group in evidence.packet()["declarations"]["groups"]:
        if group["concept"]["kind"] == "python-script-entrypoint-declaration":
            for declaration in group["declarations"]:
                value = declaration["value"]
                scripts[value["name"]] = value["target"]
    return scripts


DEPENDENCY_POLICY_BOOTSTRAP_CAPABILITY = "oh-goon-dependency-policy"


def project_script_source_path(
    root: Path,
    pyproject_text: str,
    capability: str,
) -> Path | None:
    """Project one named script capability to its owned source module.

    The script mapping is semantic authority.  A backing filename is a private
    representation detail and may change together with the mapping.
    """

    target = project_scripts_from_text(pyproject_text).get(capability)
    if target is None:
        return None
    module, separator, callable_name = target.partition(":")
    if not separator or not module or callable_name != "main":
        return None
    parts = module.split(".")
    if any(not part.isidentifier() for part in parts):
        return None
    return root / "backend" / "src" / Path(*parts).with_suffix(".py")


def dependency_policy_bootstrap_source_path(root: Path, pyproject_text: str) -> Path | None:
    """Resolve the one source module authorized for pre-environment dependency policy."""

    source = project_script_source_path(
        root, pyproject_text, DEPENDENCY_POLICY_BOOTSTRAP_CAPABILITY
    )
    if source is None:
        return None
    repository_tooling = root / "backend" / "src" / "oh_goon" / "repository_tooling"
    return source if source.is_relative_to(repository_tooling) else None


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class PythonSemanticFacts:
    """Producer source bindings projected into existing architecture policy.

    Qualified names describe authored bindings, never a proved runtime target.
    With ``imported_only=False``, native qualified symbolic receiver references
    also expose their lexical spelling for local forbidden-API shape policy.
    All conditional and unknown facts remain in the intact producer evidence;
    defaults and observed callers never specialize a parameter's call target.
    """

    calls: tuple[tuple[str, ast.Call], ...]
    references: frozenset[str]
    imports: frozenset[str]
    string_literals: tuple[str, ...]
    classes: tuple[ast.ClassDef, ...]
    call_observations: tuple[tuple[dict[str, Any], ast.Call], ...]
    evidence: HashmarksSourceEvidence


def source_binding_packet(
    evidence: HashmarksSourceEvidence, *, language: str | tuple[str, ...]
) -> dict[str, Any]:
    """Validate a detached canonical packet without retaining another representation."""
    packet = evidence.packet()
    _validate_source_binding_packet(packet, language=language)
    return packet


def _validate_source_binding_packet(
    packet: dict[str, Any], *, language: str | tuple[str, ...]
) -> None:
    """Keep native qualification checks identical for both transport forms."""
    languages = (language,) if isinstance(language, str) else language
    parser = packet.get("parser") or {}
    if (
        packet.get("schema") != "hashmarks.source-bindings.v1"
        or packet.get("language") not in languages
        or packet.get("availability") != "observed"
        or packet.get("completeness") != "complete"
        or packet.get("truncation") != "not-truncated"
        or packet.get("freshness") == "stale"
        or packet.get("coordinate_system") != "zero-based-half-open-bytes"
        or not isinstance(packet.get("facts"), list)
        or not parser.get("signature")
        or parser.get("capability", {}).get("available") is not True
        or not packet.get("observation_identity")
    ):
        raise ValueError(
            "Architecture source bindings are unavailable or incomplete: "
            + str(packet.get("reason") or packet.get("availability"))
        )


def source_import_spelling(target: dict[str, Any]) -> str:
    """Render an authored import target without changing its qualification."""
    root = "." * target.get("level", 0) + str(target.get("module") or "")
    member = target.get("name")
    if member:
        root += ("." if root and not root.endswith(".") else "") + member
    return ".".join((root, *target.get("properties", [])))


def source_import_name(fact: dict[str, Any]) -> str | None:
    """Retain an authored import declaration independently of later rebinding."""
    syntax = fact.get("import_syntax")
    return source_import_spelling(syntax) if isinstance(syntax, dict) else None


def source_binding_name(fact: dict[str, Any], *, imported_only: bool) -> str | None:
    if fact.get("qualification") == "qualified" and not fact.get("conditions"):
        target = fact.get("target")
        if isinstance(target, dict):
            properties = target.get("properties", [])
            if target.get("kind") == "import":
                # Preserve relative import spelling and unresolved module
                # qualification. Owned-package policy may compare this syntax;
                # this projection does not resolve a cross-member import.
                return source_import_spelling(target)
            if not imported_only and target.get("kind") == "declaration":
                return ".".join((target["name"], *properties))
    if imported_only:
        return None
    symbolic = fact.get("symbolic_reference")
    if (
        isinstance(symbolic, dict)
        and symbolic.get("qualification") == "qualified"
        and not symbolic.get("conditions")
        and isinstance(symbolic.get("root"), dict)
    ):
        root = symbolic["root"]
        if isinstance(root.get("name"), str):
            return ".".join((root["name"], *symbolic.get("properties", [])))
    return None


def _node_byte_range(node: ast.AST, line_offsets: list[int]) -> tuple[int, int] | None:
    if not isinstance(node, (ast.expr, ast.stmt)):
        return None
    if node.end_lineno is None or node.end_col_offset is None:
        return None
    return (
        line_offsets[node.lineno - 1] + node.col_offset,
        line_offsets[node.end_lineno - 1] + node.end_col_offset,
    )


def python_semantic_facts(
    path: Path,
    *,
    imported_only: bool = False,
    tree: ast.Module | None = None,
) -> PythonSemanticFacts:
    """Consume native binding facts; retain AST only for local structure policy.

    Conditional defaults and observed caller arguments never become an
    unconditional invocation target. Unknown call observations remain separate
    from qualified binding names for explicit architecture syntax policy.
    """
    if _PYTHON_SOURCE_SNAPSHOT.get() is None:
        with python_source_snapshot():
            return python_semantic_facts(path, imported_only=imported_only, tree=tree)
    raw = python_source_bytes(path)
    selected_tree = _python_tree(path, raw) if tree is None else tree
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    key = (path.resolve(), imported_only, selected_tree)
    if snapshot is not None and key in snapshot.semantic_facts:
        return snapshot.semantic_facts[key]
    evidence, packet = python_source_observation(path)
    if evidence.source_bytes != raw:
        raise ValueError("Producer source bytes differ from captured Python structure")
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    nodes = {
        span: node
        for node in ast.walk(selected_tree)
        if isinstance(node, ast.Call) and (span := _node_byte_range(node, offsets)) is not None
    }
    calls: list[tuple[str, ast.Call]] = []
    observations: list[tuple[dict[str, Any], ast.Call]] = []
    references: set[str] = set()
    imports: set[str] = set()
    strings: list[str] = []
    for fact in packet["facts"]:
        name = source_binding_name(fact, imported_only=imported_only)
        if fact.get("kind") == "call-site":
            span = fact["range"]
            node = nodes.get((span["start_byte"], span["end_byte"]))
            if node is None:
                raise ValueError("Producer call range differs from retained Python structure")
            observations.append((fact, node))
            if name is not None:
                calls.append((name, node))
        elif fact.get("kind") == "reference" and name is not None:
            references.add(name)
        elif fact.get("kind") == "import-binding":
            imported = source_import_name(fact)
            if imported is not None:
                imports.add(imported)
        if (
            fact.get("kind") == "literal-expression"
            and fact.get("qualification") == "qualified"
            and not fact.get("conditions")
            and isinstance(fact.get("literal"), str)
        ):
            strings.append(fact["literal"])
    facts = PythonSemanticFacts(
        calls=tuple(calls),
        references=frozenset(references),
        imports=frozenset(imports),
        string_literals=tuple(strings),
        classes=tuple(node for node in ast.walk(selected_tree) if isinstance(node, ast.ClassDef)),
        call_observations=tuple(observations),
        evidence=evidence,
    )
    if snapshot is not None:
        snapshot.semantic_facts[key] = facts
    return facts


@dataclass(slots=True)
class _PythonSourceSnapshot:
    lifetime: ExitStack
    root: Path | None = None
    captured_packets: bool = False
    captures: dict[Path, bytes] = field(default_factory=dict)
    trees: dict[Path, ast.Module] = field(default_factory=dict)
    evidence: dict[Path, HashmarksSourceEvidence] = field(default_factory=dict)
    semantic_facts: dict[tuple[Path, bool, ast.Module], PythonSemanticFacts] = field(
        default_factory=dict
    )
    typescript_facts: dict[tuple[str, str], TypeScriptFacts] = field(default_factory=dict)
    manifest_evidence: dict[str, HashmarksSourceEvidence] = field(default_factory=dict)
    native_scopes: dict[Path, object] = field(default_factory=dict)


_PYTHON_SOURCE_SNAPSHOT: ContextVar[_PythonSourceSnapshot | None] = ContextVar(
    "architecture_python_source_snapshot", default=None
)
_SOURCE_CHECKOUT_ROOT = Path(__file__).resolve().parents[5]


@contextmanager
def python_source_snapshot(
    root: Path | None = None, *, captured_packets: bool = False
) -> Generator[None]:
    """Keep one source lifetime and optionally consume native packets at first use.

    No decoded packet table is retained. Nested scopes keep their outer lifetime
    and its selected transport; later independent reads use canonical evidence.
    """
    if type(captured_packets) is not bool:
        raise ValueError("Architecture captured-packet transport must be a boolean")
    if _PYTHON_SOURCE_SNAPSHOT.get() is not None:
        yield
        return
    with ExitStack() as lifetime:
        snapshot = _PythonSourceSnapshot(
            lifetime,
            root.resolve() if root is not None else None,
            captured_packets=captured_packets,
        )
        token = _PYTHON_SOURCE_SNAPSHOT.set(snapshot)
        try:
            yield
        finally:
            _PYTHON_SOURCE_SNAPSHOT.reset(token)


def python_source_bindings(path: Path) -> HashmarksSourceEvidence:
    """Retain the existing evidence-only interface for independent consumers."""
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    resolved = path.resolve()
    if snapshot is not None and resolved in snapshot.evidence:
        return snapshot.evidence[resolved]
    evidence, _packet = python_source_observation(path)
    return evidence


def python_source_observation(path: Path) -> tuple[HashmarksSourceEvidence, dict[str, Any]]:
    """Deliver a selected source observation directly to its immediate consumer."""
    if _PYTHON_SOURCE_SNAPSHOT.get() is None:
        with python_source_snapshot():
            return python_source_observation(path)
    resolved = path.resolve()
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    assert snapshot is not None
    if resolved in snapshot.evidence:
        evidence = snapshot.evidence[resolved]
        return evidence, source_binding_packet(evidence, language="python")
    root = (
        snapshot.root
        if snapshot.root is not None and resolved.is_relative_to(snapshot.root)
        else _SOURCE_CHECKOUT_ROOT
        if resolved.is_relative_to(_SOURCE_CHECKOUT_ROOT)
        else resolved.parent
    )
    if root not in snapshot.native_scopes:
        snapshot.native_scopes[root] = snapshot.lifetime.enter_context(hashmarks_source_scope(root))
    scope = snapshot.native_scopes[root]
    relative = resolved.relative_to(root).as_posix()
    captured: list[tuple[HashmarksSourceEvidence, dict[str, Any]]] = []

    def receive(member: str, evidence: HashmarksSourceEvidence, packet: dict[str, Any]) -> None:
        if member != relative or captured:
            raise ValueError("Native source observation differs from its selected member")
        captured.append((evidence, packet))

    if snapshot.captured_packets:
        evidence = observe_source_bindings(root, (relative,), scope=scope, on_observation=receive)[
            relative
        ]
        if len(captured) != 1 or captured[0][0] is not evidence:
            raise ValueError("Native source observation has no exact evidence binding")
        packet = captured[0][1]
        _validate_source_binding_packet(packet, language="python")
    else:
        evidence = observe_source_bindings(root, (relative,), scope=scope)[relative]
        packet = source_binding_packet(evidence, language="python")
    if evidence.source_bytes is None:
        raise ValueError("Architecture Python source bytes are unavailable")
    previous = snapshot.captures.get(resolved)
    if previous is not None and previous != evidence.source_bytes:
        raise ValueError("Producer source bytes differ from captured Python structure")
    snapshot.captures.setdefault(resolved, evidence.source_bytes)
    snapshot.evidence[resolved] = evidence
    return evidence, packet


def python_ast(path: Path) -> ast.Module:
    """Inspect native Python structure for local policy, without binding inference."""
    return _python_tree(path, python_source_bytes(path))


def python_source_bytes(path: Path) -> bytes:
    """Reuse bounded captured bytes for structural policy and decoding checks."""
    resolved = path.resolve()
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    if snapshot is not None and resolved in snapshot.captures:
        return snapshot.captures[resolved]
    with resolved.open("rb") as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(8_388_609)
        after = os.fstat(stream.fileno())
    if len(raw) > 8_388_608:
        raise ValueError("Architecture Python source exceeds its capture byte bound")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("Architecture Python source changed during capture")
    if snapshot is not None:
        snapshot.captures[resolved] = raw
    return raw


def _python_tree(path: Path, raw: bytes) -> ast.Module:
    resolved = path.resolve()
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    if snapshot is not None and resolved in snapshot.trees:
        return snapshot.trees[resolved]
    tree = ast.parse(raw, filename=str(resolved))
    if snapshot is not None:
        snapshot.trees[resolved] = tree
    return tree
