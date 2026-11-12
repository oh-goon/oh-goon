"""Released source observations retain uncertainty across the public boundary."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from hashmarks.codemap.providers import TreeSitterRangeProvider
from hashmarks.codemap.repository_source import RepositorySource

from oh_goon.integrations.hashmarks import (
    HashmarksSourceEvidence,
    capture_source_member,
    hashmarks_source_scope,
    observe_make_shell_declarations,
    observe_make_shell_declarations_text,
    observe_python_project_declarations,
    observe_source_bindings,
    observe_source_bindings_text,
    require_loaded_hashmarks_runtime,
)


def test_python_metadata_does_not_fall_back_when_producer_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hashmarks.codemap import PythonUvManifestDeclarations

    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')

    def unavailable(*args: object) -> None:
        raise RuntimeError("manifest producer disabled")

    monkeypatch.setattr(PythonUvManifestDeclarations, "discover", unavailable)
    with pytest.raises(ValueError, match="manifest producer disabled"):
        observe_python_project_declarations(tmp_path)


def test_python_alias_default_and_call_keep_distinct_producer_authority() -> None:
    evidence = observe_source_bindings_text(
        "import subprocess as process\nlaunch = process.Popen\n"
        'def invoke(factory=launch):\n    return factory(["echo", "hello"])\n',
        path="pkg/source.py",
    )
    packet = evidence.packet()
    assert evidence.source_bytes == (
        b"import subprocess as process\nlaunch = process.Popen\n"
        b'def invoke(factory=launch):\n    return factory(["echo", "hello"])\n'
    )
    assert evidence.producer_implementation_identity == require_loaded_hashmarks_runtime()
    assert packet["availability"] == "observed"
    assert packet["parser"]["grammar"] == "python"
    assert packet["parser"]["signature"]
    assert packet["member"]["member_revision"]
    assert packet["observation_identity"]
    facts = packet["facts"]
    alias = next(row for row in facts if row["kind"] == "alias-binding")
    assert alias["target"]["module"] == "subprocess"
    assert alias["target"]["properties"] == ["Popen"]
    default = next(row for row in facts if row["kind"] == "default-binding")
    assert default["conditions"]
    call = next(row for row in facts if row["kind"] == "call-site")
    assert call["target"] is None
    assert call["runtime_target"] == "not-established"
    assert packet["semantic_coverage"]["state"] == "incomplete"
    assert packet["negative_evidence"] == "not-admissible"
    # Consumer mutation cannot modify the retained packet or its identities.
    packet["facts"].clear()
    assert evidence.packet()["facts"]


def test_projection_bounds_do_not_become_complete_source_knowledge(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("first = 1\nsecond = 2\n")
    packet = observe_source_bindings(tmp_path, ("source.py",), limit=1)["source.py"].packet()
    assert packet["availability"] == "observed"
    assert packet["completeness"] == "incomplete"
    assert packet["truncation"] == "truncated"
    assert packet["observed_fact_count"] > packet["returned_fact_count"] == 1
    assert packet["negative_evidence"] == "not-admissible"


@pytest.mark.parametrize("text", ("def broken(:\n", "value = (\n"))
def test_malformed_source_cannot_activate_a_consumer_parser(text: str) -> None:
    packet = observe_source_bindings_text(text, path="source.py").packet()
    assert packet["availability"] == "unavailable"
    assert packet["completeness"] == "unknown"
    assert packet["facts"] == []
    assert packet["reason"]
    assert packet["negative_evidence"] == "not-admissible"


def test_missing_member_retains_producer_unavailability(tmp_path: Path) -> None:
    evidence = observe_source_bindings(tmp_path, ("missing.py",))["missing.py"]
    packet = evidence.packet()
    assert packet["availability"] == "unavailable"
    assert packet["facts"] == []
    assert packet["member"]["state"] != "known-present"
    assert evidence.source_bytes is None


def test_disabled_producer_cannot_activate_local_source_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("canonical producer disabled")

    monkeypatch.setattr(RepositorySource, "bindings", unavailable)
    with pytest.raises(RuntimeError, match="canonical producer disabled"):
        observe_source_bindings_text("import subprocess\nsubprocess.run([])\n", path="source.py")


def test_source_change_before_structural_bytes_transport_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    native_bindings = RepositorySource.bindings

    def change_after_bindings(
        owner: RepositorySource, path: str, *, limit: int, max_bytes: int
    ) -> dict[str, object]:
        packet = native_bindings(owner, path, limit=limit, max_bytes=max_bytes)
        source.write_text("value = 2\n")
        return packet

    monkeypatch.setattr(RepositorySource, "bindings", change_after_bindings)
    delivered: list[str] = []
    with pytest.raises(ValueError, match="Source bytes differ"):
        observe_source_bindings(
            tmp_path,
            ("source.py",),
            on_observation=lambda path, _evidence, _packet: delivered.append(path),
        )
    assert delivered == []


def test_growth_during_binding_byte_transport_cannot_accumulate_beyond_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    original_bindings = RepositorySource.bindings
    original_member = RepositorySource.member

    def grow_after_bindings(
        owner: RepositorySource, path: str, *, limit: int, max_bytes: int
    ) -> dict[str, object]:
        packet = original_bindings(owner, path, limit=limit, max_bytes=max_bytes)
        source.write_bytes(b"x" * 1024)
        return packet

    def bounded_transfer(
        owner: RepositorySource,
        path: str,
        *,
        include_bytes: bool = False,
        max_bytes: int | None = None,
    ) -> tuple[dict[str, Any], bytes | None]:
        member, raw = original_member(owner, path, include_bytes=include_bytes, max_bytes=max_bytes)
        if path == "source.py":
            assert raw is None or len(raw) <= 32
        return member, raw

    monkeypatch.setattr(RepositorySource, "bindings", grow_after_bindings)
    monkeypatch.setattr(RepositorySource, "member", bounded_transfer)
    with pytest.raises(ValueError):
        observe_source_bindings(tmp_path, ("source.py",), max_bytes=32)


def test_make_qualification_rejects_drift_from_original_product_input_capture(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Makefile"
    source.write_text("target:\n\tgit --version\n", encoding="utf-8")
    with hashmarks_source_scope(tmp_path) as scope:
        original = capture_source_member(tmp_path, "Makefile", scope=scope)
        baseline = observe_make_shell_declarations(
            tmp_path,
            make_paths=("Makefile",),
            layout="file",
            selected_targets=("target",),
            source_capture=original,
            scope=scope,
        )
        assert baseline.source_bytes == original.source_bytes
        source.write_text("target:\n\tcurl remote\n", encoding="utf-8")
        with pytest.raises(ValueError, match="bytes differ"):
            observe_make_shell_declarations(
                tmp_path,
                make_paths=("Makefile",),
                layout="file",
                selected_targets=("target",),
                source_capture=original,
                scope=scope,
            )


@pytest.mark.parametrize(
    ("path", "grammar"),
    [
        ("source.js", "javascript"),
        ("source.jsx", "javascript"),
        ("source.ts", "typescript"),
        ("source.tsx", "tsx"),
    ],
)
def test_ecmascript_aliases_and_shadowing_keep_native_grammar_authority(
    path: str, grammar: str
) -> None:
    source = (
        "const transport = globalThis.fetch;\n"
        'const key = "local" + "Storage";\n'
        "const store = globalThis[key];\n"
        'const endpoint = "https://" + "example.com/api";\n'
        "transport(endpoint);\n"
        'function shadowed(fetch) { fetch("/api"); }\n'
    )
    evidence = observe_source_bindings_text(source, path=path)
    packet = evidence.packet()
    assert evidence.source_bytes == source.encode()
    assert packet["availability"] == "observed"
    assert packet["completeness"] == "complete"
    assert packet["parser"]["grammar"] == grammar
    assert packet["parser"]["signature"].endswith(":" + grammar)
    assert packet["parser"]["capability"]["available"] is True
    assert packet["parser"]["capability"]["version"]
    facts = packet["facts"]
    transport = next(
        row for row in facts if row["kind"] == "alias-binding" and row["name"] == "transport"
    )
    assert transport["target"]["kind"] == "lexical-global"
    assert transport["target"]["properties"] == ["fetch"]
    store = next(row for row in facts if row["kind"] == "alias-binding" and row["name"] == "store")
    assert store["target"]["properties"] == ["localStorage"]
    endpoint = next(
        row for row in facts if row["kind"] == "literal-binding" and row["name"] == "endpoint"
    )
    assert endpoint["literal"] == "https://example.com/api"
    shadowed = next(
        row
        for row in facts
        if row["kind"] == "call-site"
        and row["symbolic_reference"] is not None
        and row["symbolic_reference"]["root"]["name"] == "fetch"
    )
    assert shadowed["qualification"] == "unknown"
    assert shadowed["target"] is None
    assert shadowed["symbolic_reference"]["root"]["kind"] == "parameter"
    assert packet["semantic_coverage"]["state"] == "incomplete"
    assert packet["negative_evidence"] == "not-admissible"


def test_failed_canonical_ecmascript_parser_cannot_use_local_tokenization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TreeSitterRangeProvider, "syntax_tree", lambda *args: None)
    packet = observe_source_bindings_text(
        'const transport = globalThis.fetch; transport("/api");', path="source.ts"
    ).packet()
    assert packet["availability"] == "unavailable"
    assert packet["facts"] == []
    assert packet["reason"] == "native-syntax-unavailable"
    assert packet["negative_evidence"] == "not-admissible"


def test_scope_binds_one_root_and_reobserves_changed_source(tmp_path: Path) -> None:
    member = tmp_path / "source.py"
    member.write_text("value = 1\n")
    with hashmarks_source_scope(tmp_path) as scope:
        before = observe_source_bindings(tmp_path, ("source.py",), scope=scope)["source.py"]
        member.write_text("value = 2\n")
        after = observe_source_bindings(tmp_path, ("source.py",), scope=scope)["source.py"]
        assert after.packet()["availability"] == "observed"
        assert (
            after.packet()["member"]["member_revision"]
            != before.packet()["member"]["member_revision"]
        )
        assert after.packet()["member"]["index_state"] == "unindexed"
        assert after.packet()["freshness"] == "unknown"
        assert after.source_bytes == b"value = 2\n"
        with pytest.raises(ValueError, match="exact-root"):
            observe_source_bindings(tmp_path / "other", ("source.py",), scope=scope)
    with pytest.raises(ValueError, match="active"):
        observe_source_bindings(tmp_path, ("source.py",), scope=scope)
    refreshed = observe_source_bindings(tmp_path, ("source.py",))["source.py"].packet()
    assert refreshed["member"]["member_revision"] != before.packet()["member"]["member_revision"]


def _command_observations(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        declaration["value"]["static_binding_observation"]
        for group in packet["declarations"]["groups"]
        if group["concept"]["kind"] == "shell-command-expression"
        for declaration in group["declarations"]
    ]


def test_file_layout_retains_large_source_facts_without_effective_command_claims() -> None:
    evidence = observe_make_shell_declarations_text(
        "".join(f"echo source{index}\n" for index in range(160)),
        path="fixture.sh",
        language="shell",
        layout="file",
    )
    packet = evidence.packet()
    groups = packet["declarations"]["groups"]
    assert len(groups) == 1
    value = groups[0]["declarations"][0]["value"]
    assert len(value["facts"]) == value["observed_fact_count"] == 160
    assert value["facts_completeness"] == "complete-supported-subset"
    assert value["facts_truncation"] == "not-truncated"
    assert value["negative_evidence_admissible"] is False
    assert all(row["value"]["execution"] == "not-established" for row in value["facts"])


def test_make_indirection_remains_conditional_source_evidence() -> None:
    packet = observe_make_shell_declarations_text(
        'RUNNER := python\nTOOL = $(RUNNER)\ncheck:\n\tMODE="sample" $(TOOL) -m demo\n',
        path="Makefile",
        language="make",
    ).packet()
    commands = _command_observations(packet)
    assert commands[0]["words"] == ["python", "-m", "demo"]
    assert commands[0]["state"] == "conditional-static"
    assert commands[0]["assumptions"]
    assert commands[0]["semantic_coverage"] == "incomplete"
    provenance = packet["providers"][0]["provenance"]
    assert provenance["semantics"] == "hashmarks.make-shell-syntax.v5"
    assert provenance["effective_environment"] == "not-established"
    assert provenance["execution"] == "not-performed"
    assert provenance["negative_evidence_admissible"] is False


def test_malformed_shell_refuses_without_a_whitespace_fallback() -> None:
    with pytest.raises(ValueError, match="unterminated.*quoting"):
        observe_make_shell_declarations_text(
            'echo "unterminated\n', path="check.sh", language="shell"
        )


def test_unresolved_shell_never_becomes_known_command_words() -> None:
    packet = observe_make_shell_declarations_text(
        'echo "$UNBOUND"\n', path="check.sh", language="shell"
    ).packet()
    commands = _command_observations(packet)
    assert commands
    assert all(row["state"] == "unclassified" for row in commands)
    assert all("words" not in row for row in commands)


@pytest.mark.parametrize("path", ("../escape.py", "/outside.py", "a//source.py", ""))
def test_text_capture_rejects_noncanonical_member_authority(path: str) -> None:
    with pytest.raises(ValueError, match="relative member"):
        observe_source_bindings_text("value = 1", path=path)


def test_native_packet_delivery_preserves_canonical_evidence_and_public_detachment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.py"
    source = b"import subprocess\nsubprocess.run([])\n"
    path.write_bytes(source)
    delivered: list[tuple[str, HashmarksSourceEvidence, dict[str, Any]]] = []

    def receive(member: str, evidence: HashmarksSourceEvidence, packet: dict[str, Any]) -> None:
        assert evidence.source_bytes == source
        assert evidence.producer_implementation_identity == require_loaded_hashmarks_runtime()
        assert packet == evidence.packet()
        assert packet["facts"]
        delivered.append((member, evidence, packet))
        packet["facts"].clear()

    with hashmarks_source_scope(tmp_path) as scope:
        result = observe_source_bindings(
            tmp_path, ("source.py",), scope=scope, on_observation=receive
        )
        assert len(delivered) == 1 and delivered[0][0] == "source.py"
        assert delivered[0][1] is result["source.py"]
        assert delivered[0][2]["facts"] == []
        detached = result["source.py"].packet()
        assert detached["facts"]
        detached["facts"].clear()
        assert result["source.py"].packet()["facts"]
        assert observe_source_bindings(tmp_path, ("source.py",), scope=scope)["source.py"].packet()[
            "facts"
        ]


def test_native_packet_receiver_does_not_obtain_repository_private_source(tmp_path: Path) -> None:
    secret = "PRIVATE_SOURCE_MUST_NOT_ENTER_EVIDENCE"
    (tmp_path / "private.py").write_text(f"SECRET = {secret!r}\n")
    (tmp_path / ".hashmarks-context.toml").write_text(
        '[[rule]]\npattern = "private.py"\nindex = false\nvisibility = "deny"\n'
    )
    delivered: list[tuple[HashmarksSourceEvidence, dict[str, Any]]] = []
    result = observe_source_bindings(
        tmp_path,
        ("private.py",),
        on_observation=lambda _path, evidence, packet: delivered.append((evidence, packet)),
    )
    assert len(delivered) == 1 and delivered[0][0] is result["private.py"]
    evidence, packet = delivered[0]
    assert evidence.source_bytes is None and packet["facts"] == []
    assert packet["availability"] == "unavailable"
    assert secret not in json.dumps(packet)
    assert evidence.packet() == packet


def test_invalid_native_packet_receiver_refuses_before_scope_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations.hashmarks import source_facts as owner

    monkeypatch.setattr(
        owner,
        "hashmarks_source_scope",
        lambda _root: pytest.fail("invalid receiver opened a scope"),
    )
    invalid: Any = object()
    with pytest.raises(ValueError, match="receiver must be callable"):
        observe_source_bindings(tmp_path, ("source.py",), on_observation=invalid)


def test_native_packet_receiver_failure_stops_work_and_closes_its_owned_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations.hashmarks import source_facts as owner

    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text("value = 1\n")
    native = owner.hashmarks_source_scope
    scopes: list[object] = []
    delivered: list[str] = []

    @contextmanager
    def observe(root: Path) -> Iterator[object]:
        with native(root) as scope:
            scopes.append(scope)
            yield scope

    def fail(member: str, _evidence: HashmarksSourceEvidence, _packet: dict[str, Any]) -> None:
        delivered.append(member)
        raise RuntimeError("native packet receiver failed")

    monkeypatch.setattr(owner, "hashmarks_source_scope", observe)
    with pytest.raises(RuntimeError, match="receiver failed"):
        observe_source_bindings(tmp_path, ("first.py", "second.py"), on_observation=fail)
    assert delivered == ["first.py"] and len(scopes) == 1
    with pytest.raises(ValueError, match="active"):
        observe_source_bindings(tmp_path, ("first.py",), scope=scopes[0])


def test_native_packet_receiver_failure_keeps_a_caller_owned_scope_usable(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("value = 1\n")

    def fail(*_arguments: object) -> None:
        raise RuntimeError("native packet receiver failed")

    with hashmarks_source_scope(tmp_path) as scope:
        with pytest.raises(RuntimeError, match="receiver failed"):
            observe_source_bindings(tmp_path, ("source.py",), scope=scope, on_observation=fail)
        assert (
            observe_source_bindings(tmp_path, ("source.py",), scope=scope)["source.py"].source_bytes
            == b"value = 1\n"
        )


@pytest.mark.parametrize("fault", ["producer", "policy"])
def test_native_packet_receiver_cannot_continue_after_observation_authority_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from oh_goon.integrations.hashmarks import source_facts as owner

    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text("value = 1\n")
    delivered: list[str] = []

    def change(member: str, _evidence: HashmarksSourceEvidence, _packet: dict[str, Any]) -> None:
        delivered.append(member)
        if fault == "producer":
            monkeypatch.setattr(owner, "require_loaded_hashmarks_runtime", lambda: "foreign")
        else:
            (tmp_path / ".hashmarks-context.toml").write_text(
                '[[rule]]\npattern = "second.py"\nindex = false\nvisibility = "deny"\n'
            )

    with pytest.raises(ValueError, match="producer changed|policy changed"):
        observe_source_bindings(tmp_path, ("first.py", "second.py"), on_observation=change)
    assert delivered == ["first.py"]
