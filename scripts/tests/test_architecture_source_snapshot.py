from __future__ import annotations

import ast
import os
from typing import Any

import pytest
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    python_ast,
    python_semantic_facts,
    python_source_snapshot,
)
from oh_goon.repository_tooling.architecture.typescript_semantics import (
    project_typescript,
)


def test_each_invocation_observes_equal_size_timestamp_preserving_edits(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.py"
    path.write_text("VALUE = 1\n")
    metadata = path.stat()
    with python_source_snapshot():
        assert ast.unparse(python_ast(path)) == "VALUE = 1"
    path.write_text("VALUE = 2\n")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    with python_source_snapshot():
        assert ast.unparse(python_ast(path)) == "VALUE = 2"
    path.write_text("VALUE = 3\n")
    assert ast.unparse(python_ast(path)) == "VALUE = 3"


def test_checks_share_one_source_read_within_the_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "source.py"
    path.write_text("VALUE = 1\n")
    native = Path.open
    reads = []

    def observe_read(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == path:
            reads.append(self)
        return native(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe_read)
    with python_source_snapshot():
        first = python_ast(path)
        with python_source_snapshot():
            assert python_ast(path) is first
    assert reads == [path]


def test_repeated_python_projection_reuses_evidence_without_rebinding_caller_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations.hashmarks import HashmarksSourceEvidence

    path = tmp_path / "source.py"
    source = "from subprocess import Popen\ndef launch():\n    Popen([])\nlaunch()\n"
    path.write_text(source)
    native = HashmarksSourceEvidence.packet
    observations: list[bytes | None] = []

    def packet(self: HashmarksSourceEvidence) -> dict[str, Any]:
        observations.append(self.source_bytes)
        return native(self)

    monkeypatch.setattr(HashmarksSourceEvidence, "packet", packet)
    with python_source_snapshot():
        imported = python_semantic_facts(path, imported_only=True)
        observed = len(observations)
        assert observed > 0
        assert [name for name, _ in imported.calls] == ["subprocess.Popen"]
        assert python_semantic_facts(path, imported_only=True).calls == imported.calls
        assert len(observations) == observed
        assert {name for name, _ in python_semantic_facts(path).calls} == {
            "subprocess.Popen",
            "launch",
        }
        caller_tree = ast.parse(source)
        projected = python_semantic_facts(path, imported_only=True, tree=caller_tree)
        caller_nodes = set(ast.walk(caller_tree))
        assert projected.calls
        assert all(call in caller_nodes for _, call in projected.calls)
        assert all(call not in caller_nodes for _, call in imported.calls)
    path.write_text(source.replace("Popen", "start"))
    with python_source_snapshot():
        assert [
            name for name, _ in python_semantic_facts(path, imported_only=True).calls
        ] == ["subprocess.start"]
    assert len(observations) > observed


def test_typescript_projection_reuses_only_exact_inputs_within_one_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.integrations.hashmarks import observe_source_bindings_text
    from oh_goon.repository_tooling.architecture import typescript_semantics as owner

    native = observe_source_bindings_text
    observations: list[tuple[str, str]] = []

    def observe(text: str, *, path: str) -> Any:
        observations.append((path, text))
        return native(text, path=path)

    monkeypatch.setattr(owner, "observe_source_bindings_text", observe)
    first = "window.localStorage.setItem('run-id', 'private');"
    changed = "window.sessionStorage.setItem('run-id', 'private');"
    with python_source_snapshot():
        assert project_typescript(first, path="source.ts").uses("localStorage")
        assert project_typescript(first, path="source.ts").uses("localStorage")
        assert observations == [("source.ts", first)]
        assert project_typescript(changed, path="source.ts").uses("sessionStorage")
        other = project_typescript(first, path="other.ts")
        assert other.uses("localStorage")
        assert other.evidence.packet()["member"]["path"] == "other.ts"
        assert observations == [
            ("source.ts", first),
            ("source.ts", changed),
            ("other.ts", first),
        ]
    with python_source_snapshot():
        assert project_typescript(first, path="source.ts").uses("localStorage")
    assert observations[-1] == ("source.ts", first)
    assert len(observations) == 4
