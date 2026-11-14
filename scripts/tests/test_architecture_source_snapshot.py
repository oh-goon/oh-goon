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
    python_source_observation,
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


@pytest.mark.parametrize("captured_packets", [False, True])
def test_repeated_python_projection_reuses_evidence_without_rebinding_caller_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_packets: bool
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
    with python_source_snapshot(captured_packets=captured_packets):
        imported = python_semantic_facts(path, imported_only=True)
        observed = len(observations)
        assert (observed == 0) is captured_packets
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
    metadata = path.stat()
    path.write_text(source.replace("Popen", "start"))
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    with python_source_snapshot(captured_packets=captured_packets):
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


@pytest.mark.parametrize("captured_packets", [False, True])
def test_source_observation_is_one_use_and_canonical_reads_remain_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_packets: bool
) -> None:
    from oh_goon.integrations.hashmarks import HashmarksSourceEvidence

    path = tmp_path / "source.py"
    path.write_text("import subprocess\nsubprocess.run([])\n")
    native = HashmarksSourceEvidence.packet
    observations: list[HashmarksSourceEvidence] = []

    def observe(self: HashmarksSourceEvidence) -> dict[str, Any]:
        observations.append(self)
        return native(self)

    monkeypatch.setattr(HashmarksSourceEvidence, "packet", observe)
    with python_source_snapshot(tmp_path, captured_packets=captured_packets):
        evidence, packet = python_source_observation(path)
        assert packet["facts"]
        assert len(observations) == (0 if captured_packets else 1)
        detached = evidence.packet()
        detached["facts"].clear()
        assert packet["facts"]
        with pytest.raises(ValueError, match="unavailable or incomplete"):
            source_binding_packet(evidence, language="typescript")
        packet["facts"].clear()
        packet["availability"] = "unavailable"
        returned, refreshed = python_source_observation(path)
        assert returned is evidence
        assert refreshed["facts"] and refreshed["availability"] == "observed"
        assert source_binding_packet(evidence, language="python") == refreshed
        assert source_binding_packet(evidence, language="python") is not refreshed


@pytest.mark.parametrize("outer_captured", [False, True])
@pytest.mark.parametrize("inner_captured", [False, True])
def test_nested_source_scopes_preserve_the_outer_transport_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_captured: bool,
    inner_captured: bool,
) -> None:
    from oh_goon.integrations.hashmarks import HashmarksSourceEvidence

    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text("first = 1\n")
    second.write_text("second = 2\n")
    native = HashmarksSourceEvidence.packet
    observations: list[HashmarksSourceEvidence] = []

    def observe(self: HashmarksSourceEvidence) -> dict[str, Any]:
        observations.append(self)
        return native(self)

    monkeypatch.setattr(HashmarksSourceEvidence, "packet", observe)
    with python_source_snapshot(tmp_path, captured_packets=outer_captured):
        before, _packet = python_source_observation(first)
        with python_source_snapshot(tmp_path, captured_packets=inner_captured):
            after, _packet = python_source_observation(second)
    assert before.source_bytes == b"first = 1\n"
    assert after.source_bytes == b"second = 2\n"
    assert len(observations) == (0 if outer_captured else 2)


@pytest.mark.parametrize("captured_packets", [False, True])
def test_native_unavailability_cannot_be_promoted_by_packet_transport(
    tmp_path: Path, captured_packets: bool
) -> None:
    path = tmp_path / "source.py"
    path.write_text("def broken(:\n")
    with python_source_snapshot(tmp_path, captured_packets=captured_packets):
        with pytest.raises(ValueError, match="unavailable or incomplete"):
            python_source_observation(path)


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "wrong-path", "substituted-evidence"]
)
def test_native_packet_handoff_requires_its_exact_delivery_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from dataclasses import replace
    from collections.abc import Callable, Sequence
    from oh_goon.integrations.hashmarks import (
        HashmarksSourceEvidence,
        observe_source_bindings,
    )
    from oh_goon.repository_tooling.architecture import common

    path = tmp_path / "source.py"
    path.write_text("value = 1\n")
    native = observe_source_bindings

    def observe(
        root: Path,
        paths: Sequence[str],
        *,
        scope: object,
        on_observation: Callable[[str, HashmarksSourceEvidence, dict[str, Any]], None],
    ) -> dict[str, HashmarksSourceEvidence]:
        def deliver(
            member: str, evidence: HashmarksSourceEvidence, packet: dict[str, Any]
        ) -> None:
            if fault == "missing":
                return
            on_observation(
                "other.py" if fault == "wrong-path" else member,
                replace(evidence) if fault == "substituted-evidence" else evidence,
                packet,
            )
            if fault == "duplicate":
                on_observation(member, evidence, packet)

        return native(root, paths, scope=scope, on_observation=deliver)

    monkeypatch.setattr(common, "observe_source_bindings", observe)
    with python_source_snapshot(tmp_path, captured_packets=True):
        with pytest.raises(ValueError, match="Native source observation"):
            python_source_observation(path)


@pytest.mark.parametrize("mode", [0, 1, None, "ram"])
def test_invalid_transport_mode_is_refused_before_opening_a_lifetime(mode: Any) -> None:
    with pytest.raises(ValueError, match="must be a boolean"):
        with python_source_snapshot(captured_packets=mode):
            pytest.fail("invalid transport entered a source lifetime")
