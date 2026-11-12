"""Confidentiality is an admitted scope enforced by the real native producer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hashmarks import CodeMap
from hashmarks.codemap.repository_source import RepositorySource

from oh_goon.integrations.hashmarks import (
    hashmarks_source_scope,
    observe_source_bindings,
    repository_observation,
)
from oh_goon.integrations.hashmarks.repository_scope import repository_evidence_scope

_PRIVATE_MEMBER = "docs/certification/answers/canary.py"
_CANARY = "synthetic_grader_answer_canary"


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "captured" / "repo"
    root.mkdir(parents=True)
    private = root / _PRIVATE_MEMBER
    private.parent.mkdir(parents=True)
    private.write_text(f'def answer():\n    return "needle {_CANARY}"\n', encoding="utf-8")
    (root / "public.py").write_text('def public():\n    return "needle public"\n', encoding="utf-8")
    return root


@pytest.mark.parametrize("private_member", [_PRIVATE_MEMBER, "nested/" + _PRIVATE_MEMBER])
def test_private_source_is_withdrawn_from_a_preindexed_reopened_native_map(
    tmp_path: Path, private_member: str
) -> None:
    repository = _repository(tmp_path)
    if private_member != _PRIVATE_MEMBER:
        target = repository / private_member
        target.parent.mkdir(parents=True)
        (repository / _PRIVATE_MEMBER).rename(target)
    state = tmp_path / "state"
    with CodeMap(repository, state_dir=state) as unfiltered:
        unfiltered.index.sync()
        member, raw = unfiltered.source.member(private_member, include_bytes=True)
        assert member["state"] == "known-present"
        assert raw is not None and _CANARY.encode() in raw

    for _reopen in range(2):
        with repository_evidence_scope(repository, state_dir=state) as scope:
            member, raw = scope.native.source.member(private_member, include_bytes=True)
            assert member["state"] == "unsupported"
            assert member["reason"] == "repository-evidence-denied"
            assert raw is None
            packet = scope.native.search.packet("needle", limit=10)
            assert _CANARY not in json.dumps(packet)
            results = packet["results"]
            assert isinstance(results, list)
            assert {row["path"] for row in results} == {"public.py"}
            paths = {item.rel for item in scope.native.files.iter_files()}
            assert private_member not in paths
            assert "public.py" in paths


def test_public_repository_tools_and_exact_source_reads_keep_private_members_denied(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    listing = repository_observation(
        repository.parent, tool="list_paths", query=None, capture_identity="synthetic-capture"
    )
    assert "repo/public.py" in listing["paths"]
    assert "repo/" + _PRIVATE_MEMBER not in listing["paths"]
    searched = repository_observation(
        repository.parent, tool="search_text", query="needle", capture_identity="synthetic-capture"
    )
    assert _CANARY not in json.dumps(searched)
    evidence = observe_source_bindings(repository, (_PRIVATE_MEMBER,))[_PRIVATE_MEMBER]
    assert evidence.source_bytes is None
    assert evidence.packet()["member"]["state"] == "unsupported"
    assert evidence.packet()["member"]["reason"] == "repository-evidence-denied"

    correlation = repository_observation(
        repository.parent,
        tool="correlate_evidence",
        query=json.dumps(
            {
                "bundles": [
                    {
                        "bundle_id": "diagnostic",
                        "producer": {"kind": "test"},
                        "anchors": [{"anchor_id": "private", "path": _PRIVATE_MEMBER, "line": 2}],
                    }
                ],
                "include_relationships": False,
            }
        ),
        capture_identity="synthetic-capture",
    )["correlation"]
    assert _CANARY not in json.dumps(correlation)
    anchor = correlation["bundles"][0]["anchors"][0]
    assert anchor["source_equivalence"]["state"] == "unknown"
    assert anchor["resolution"]["state"] == "unresolved"


def test_repository_deny_rules_survive_and_late_allow_rules_cannot_widen_private_scope(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "hidden.py").write_text("hidden = True\n", encoding="utf-8")
    policy = repository / ".hashmarks-context.toml"
    policy.write_bytes(
        b"# Preserve repository-owned rules and native TOML handling.\r\n"
        b'[[rule]]\r\npattern = "hidden.py"\r\nindex = false\r\n'
        b'[[rule]]\r\npattern = "docs/certification/answers/**"\r\n'
        b'index = true\r\nvisibility = "source"\r\n'
    )
    with repository_evidence_scope(repository) as scope:
        scope.native.index.sync()
        for path in ("hidden.py", _PRIVATE_MEMBER):
            member, raw = scope.native.source.member(path, include_bytes=True)
            assert member["state"] == "unsupported"
            assert raw is None
        member, raw = scope.native.source.member("public.py", include_bytes=True)
        assert member["state"] == "known-present"
        assert raw is not None


@pytest.mark.parametrize("existing_policy", [False, True])
def test_policy_drift_refuses_a_completed_native_observation(
    tmp_path: Path, existing_policy: bool
) -> None:
    repository = _repository(tmp_path)
    policy = repository / ".hashmarks-context.toml"
    if existing_policy:
        policy.write_text("# original policy\n", encoding="utf-8")
    with pytest.raises(ValueError, match="policy changed"):
        with repository_evidence_scope(repository) as scope:
            member, _raw = scope.native.source.member("public.py")
            assert member["state"] == "known-present"
            policy.write_text('[[rule]]\npattern="public.py"\nindex=false\n', encoding="utf-8")


def test_long_lived_source_scope_refuses_policy_drift_before_returning_another_packet(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    rejected_inside_scope = False
    with pytest.raises(ValueError, match="policy changed"):
        with hashmarks_source_scope(repository) as scope:
            initial = observe_source_bindings(repository, ("public.py",), scope=scope)
            assert initial["public.py"].source_bytes is not None
            (repository / ".hashmarks-context.toml").write_text(
                '[[rule]]\npattern="public.py"\nindex=false\n', encoding="utf-8"
            )
            with pytest.raises(ValueError, match="policy changed"):
                observe_source_bindings(repository, ("public.py",), scope=scope)
            rejected_inside_scope = True
    assert rejected_inside_scope


def test_policy_change_during_native_source_observation_cannot_publish_a_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    native = RepositorySource.bindings

    def observe_then_restrict(
        owner: RepositorySource, path: str, *, limit: int, max_bytes: int
    ) -> dict[str, object]:
        packet = native(owner, path, limit=limit, max_bytes=max_bytes)
        (repository / ".hashmarks-context.toml").write_text(
            '[[rule]]\npattern="public.py"\nindex=false\n', encoding="utf-8"
        )
        return packet

    monkeypatch.setattr(RepositorySource, "bindings", observe_then_restrict)
    with pytest.raises(ValueError, match="policy changed"):
        observe_source_bindings(repository, ("public.py",))


@pytest.mark.parametrize("selection", ["private-root", "nested-private-root", "symlink-root"])
def test_private_root_cannot_be_rebased_to_make_its_members_public(
    tmp_path: Path, selection: str
) -> None:
    repository = _repository(tmp_path)
    private = repository / _PRIVATE_MEMBER
    selected = private.parent
    if selection == "nested-private-root":
        selected = private.parent / "nested"
        selected.mkdir()
    elif selection == "symlink-root":
        selected = tmp_path / "alias"
        selected.symlink_to(private.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="Grader-private"):
        with repository_evidence_scope(selected):
            pytest.fail("Private source was admitted as a repository evidence root")


@pytest.mark.parametrize("restore_before_return", [False, True])
def test_constructor_root_exchange_cannot_rebind_the_native_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_before_return: bool
) -> None:
    repository = _repository(tmp_path)
    private = tmp_path / "other" / "docs" / "certification" / "answers"
    private.mkdir(parents=True)
    retained = repository.with_name("retained-repository")

    def exchange_and_open(
        workspace: Path,
        *,
        policy_path: Path | None = None,
        state_dir: Path | None = None,
        artifact_db: Path | None = None,
    ) -> CodeMap:
        workspace.rename(retained)
        workspace.symlink_to(private, target_is_directory=True)
        native = CodeMap(
            workspace, policy_path=policy_path, state_dir=state_dir, artifact_db=artifact_db
        )
        if restore_before_return:
            workspace.unlink()
            retained.rename(workspace)
        return native

    monkeypatch.setattr("hashmarks.CodeMap", exchange_and_open)
    with pytest.raises(ValueError, match="root changed|workspace differs"):
        with repository_evidence_scope(repository, state_dir=tmp_path / "state"):
            pytest.fail("A different native workspace was admitted for source queries")


def test_retained_scope_refuses_a_root_exchanged_for_a_private_symlink(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    private = tmp_path / "other" / "docs" / "certification" / "answers"
    private.mkdir(parents=True)
    rejected_inside_scope = False

    with pytest.raises(ValueError, match="root changed"):
        with repository_evidence_scope(repository) as scope:
            member, _raw = scope.native.source.member("public.py")
            assert member["state"] == "known-present"
            repository.rename(repository.with_name("retained-repository"))
            repository.symlink_to(private, target_is_directory=True)
            with pytest.raises(ValueError, match="root changed"):
                scope.verify_policy()
            rejected_inside_scope = True
    assert rejected_inside_scope


def test_stable_public_alias_and_native_state_creation_preserve_the_root_binding(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    alias = tmp_path / "public-alias"
    alias.symlink_to(repository, target_is_directory=True)

    with repository_evidence_scope(alias) as scope:
        assert scope.native.workspace == repository
        assert scope.native.state_dir.is_dir()
        member, raw = scope.native.source.member("public.py", include_bytes=True)
        assert member["state"] == "known-present"
        assert raw is not None
        scope.verify_policy()


@pytest.mark.parametrize("policy_kind", ["symlink", "oversized", "malformed"])
def test_unusable_repository_policy_never_becomes_default_admission(
    tmp_path: Path, policy_kind: str
) -> None:
    repository = _repository(tmp_path)
    policy = repository / ".hashmarks-context.toml"
    if policy_kind == "symlink":
        external = tmp_path / "external-policy.toml"
        external.write_text("# external source\n", encoding="utf-8")
        policy.symlink_to(external)
    elif policy_kind == "oversized":
        policy.write_bytes(b"#" * 1_048_577)
    else:
        policy.write_text("[[rule]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        with repository_evidence_scope(repository):
            pytest.fail("Invalid policy silently selected default admission")
