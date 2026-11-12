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
    source_binding_packet,
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


@pytest.mark.parametrize("packet_cache_bytes", [0, 1_048_576])
def test_repeated_python_projection_reuses_evidence_without_rebinding_caller_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packet_cache_bytes: int
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
    with python_source_snapshot(packet_cache_bytes=packet_cache_bytes):
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
    with python_source_snapshot(packet_cache_bytes=packet_cache_bytes):
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


def test_source_packet_reuse_keeps_language_validation_and_detached_public_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.integrations.hashmarks import (
        HashmarksSourceEvidence,
        observe_source_bindings_text,
    )

    evidence = observe_source_bindings_text("fetch('/api');", path="source.ts")
    cache_bytes = len(evidence.canonical_packet_json.encode("utf-8"))
    native = HashmarksSourceEvidence.packet
    observations: list[HashmarksSourceEvidence] = []

    def observe(self: HashmarksSourceEvidence) -> dict[str, Any]:
        observations.append(self)
        return native(self)

    monkeypatch.setattr(HashmarksSourceEvidence, "packet", observe)
    with python_source_snapshot(packet_cache_bytes=cache_bytes):
        packet = source_binding_packet(evidence, language="typescript")
        assert packet["facts"]
        assert (
            source_binding_packet(evidence, language=("typescript", "javascript"))
            is packet
        )
        assert observations == [evidence]
        with pytest.raises(ValueError, match="unavailable or incomplete"):
            source_binding_packet(evidence, language="python")
        assert observations == [evidence]
        detached = evidence.packet()
        detached["facts"].clear()
        detached["availability"] = "unavailable"
        assert source_binding_packet(evidence, language="typescript")["facts"]
        assert (
            source_binding_packet(evidence, language="typescript")["availability"]
            == "observed"
        )
    assert len(observations) == 2
    with python_source_snapshot(packet_cache_bytes=cache_bytes):
        refreshed = source_binding_packet(evidence, language="typescript")
        assert refreshed == packet and refreshed is not packet
    assert len(observations) == 3


@pytest.mark.parametrize("budget", [None, 0, "too-small", "exact", "ample"])
def test_decoded_packet_retention_requires_an_explicit_utf8_byte_budget(
    budget: str | int | None,
) -> None:
    from oh_goon.integrations.hashmarks import observe_source_bindings_text

    evidence = observe_source_bindings_text("const label = 'å';", path="source.ts")
    encoded_bytes = len(evidence.canonical_packet_json.encode("utf-8"))
    characters = len(evidence.canonical_packet_json)
    assert encoded_bytes > characters
    limits = {
        "too-small": characters,
        "exact": encoded_bytes,
        "ample": encoded_bytes * 2,
    }
    lifetime = (
        python_source_snapshot()
        if budget is None
        else python_source_snapshot(
            packet_cache_bytes=limits[budget] if isinstance(budget, str) else budget
        )
    )
    with lifetime:
        first = source_binding_packet(evidence, language="typescript")
        repeated = source_binding_packet(evidence, language="typescript")
        assert repeated == first
        assert (repeated is first) is (budget in {"exact", "ample"})


def test_packet_budget_overflow_does_not_evict_or_change_observations() -> None:
    from oh_goon.integrations.hashmarks import observe_source_bindings_text

    first = observe_source_bindings_text("fetch('/api');", path="first.ts")
    second = observe_source_bindings_text("window.localStorage;", path="second.ts")
    cache_bytes = len(first.canonical_packet_json.encode("utf-8"))
    with python_source_snapshot(packet_cache_bytes=cache_bytes):
        retained = source_binding_packet(first, language="typescript")
        overflow = source_binding_packet(second, language="typescript")
        repeated = source_binding_packet(second, language="typescript")
        assert overflow == repeated and overflow is not repeated
        assert source_binding_packet(first, language="typescript") is retained


@pytest.mark.parametrize("outer_enabled", [False, True])
@pytest.mark.parametrize("inner_enabled", [False, True])
def test_nested_source_scopes_preserve_the_outer_packet_budget(
    outer_enabled: bool, inner_enabled: bool
) -> None:
    from oh_goon.integrations.hashmarks import observe_source_bindings_text

    evidence = observe_source_bindings_text("fetch('/api');", path="source.ts")
    cache_bytes = len(evidence.canonical_packet_json.encode("utf-8"))
    with python_source_snapshot(packet_cache_bytes=cache_bytes if outer_enabled else 0):
        outer = source_binding_packet(evidence, language="typescript")
        with python_source_snapshot(
            packet_cache_bytes=cache_bytes if inner_enabled else 0
        ):
            inner = source_binding_packet(evidence, language="typescript")
            assert inner == outer
            assert (inner is outer) is outer_enabled
        after = source_binding_packet(evidence, language="typescript")
        assert after == outer
        assert (after is outer) is outer_enabled


@pytest.mark.parametrize("budget", [-1, True, 0.5])
def test_invalid_packet_budget_is_refused_before_opening_a_lifetime(
    budget: Any,
) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        with python_source_snapshot(packet_cache_bytes=budget):
            pytest.fail("invalid packet budget entered a source lifetime")
