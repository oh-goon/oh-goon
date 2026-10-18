"""AG-02A prework capture is a read-once contract, not agent execution."""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenAgentSourceCapture
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _case(tmp_path: Path):
    goon = tmp_path / "goons" / "agent" / "capture"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# capture\n")
    (goon / "goon.yml").write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- run: echo not-executable
""")
    plan = tmp_path / "plan.yml"
    plan.write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/capture]\n")
    repo_root = tmp_path / "repo-root"
    repo = repo_root / "team" / "app"
    repo.mkdir(parents=True)
    (repo / "data.txt").write_bytes(b"private task source\n")
    skill_root = tmp_path / "skill-root"
    skill = skill_root / "team" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"# Use this skill\n")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_bytes(b"guide\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, sha = resolver.input_definitions_with_source(plan)
    kw = dict(
        inputs={"repo": "local:team/app", "skill": "local:team/review", "acceptance": "review"},
        repository_root=repo_root,
        skill_root=skill_root,
        allowed_acceptance=("review",),
        expected_source_sha256=sha,
    )
    return resolver, plan, kw, repo, skill


def test_original_resolver_captures_exact_bytes_once_without_enabling_agent_step(tmp_path):
    resolver, plan, kw, repo, skill = _case(tmp_path)
    captured = resolver.capture_agent_sources(plan, **kw)
    assert isinstance(captured, FrozenAgentSourceCapture)
    assert captured.authority == "CAPTURED_PREWORK_NOT_ADMITTED"
    assert captured.transported_immutable_snapshot is False
    assert captured.capture_identity
    assert [(m.path, m.data) for m in captured.repository.members] == [
        ("data.txt", b"private task source\n")
    ]
    assert [(m.path, m.data) for m in captured.skill.members] == [
        ("SKILL.md", b"# Use this skill\n"),
        ("references/guide.md", b"guide\n"),
    ]
    first_id = captured.capture_identity
    (repo / "data.txt").write_bytes(b"different bytes\n")
    (skill / "SKILL.md").write_bytes(b"# Changed\n")
    # No re-read by capture verification; and old content remains sealed.
    captured.verify()
    assert captured.capture_identity == first_id
    assert captured.repository.members[0].data == b"private task source\n"
    assert resolver.capture_agent_sources(plan, **kw).capture_identity != first_id
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve(plan, inputs=kw["inputs"])


def test_capture_open_count_equals_member_count_not_twice(tmp_path, monkeypatch):
    resolver, plan, kw, _, _ = _case(tmp_path)
    original_open = os.open
    opened_files = []

    def spy(path, flags, *args, **kwargs):
        if not (flags & os.O_DIRECTORY):
            opened_files.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    captured = resolver.capture_agent_sources(plan, **kw)
    assert sorted(opened_files) == ["SKILL.md", "data.txt", "guide.md"]
    captured.verify()
    assert len(opened_files) == 3


def test_capture_fd_anchoring_rejects_ancestor_swap_without_reading_foreign_source(
    tmp_path, monkeypatch
):
    resolver, plan, kw, repo, _ = _case(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.txt").write_bytes(b"NEVER READ SECRET\n")
    original_open = os.open
    swapped = False

    def replace_parent(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "data.txt" and not swapped:
            swapped = True
            repo.rename(repo.with_name("old-app"))
            repo.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_parent)
    with pytest.raises(FleetPlanInvalid, match="directory changed|source ancestry changed"):
        resolver.capture_agent_sources(plan, **kw)
    assert swapped


def test_frozen_bytes_reject_tamper_and_keep_raw_data_out_of_repr(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    captured = resolver.capture_agent_sources(plan, **kw)
    assert "private task source" not in repr(captured)
    member = captured.repository.members[0]
    with pytest.raises(FrozenInstanceError):
        member.data = b"evil"
    object.__setattr__(member, "data", b"evil")
    with pytest.raises(FleetPlanInvalid, match="digest mismatch"):
        captured.verify()


def test_capture_rejects_stale_source_or_unavailable_remote_acquisition(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="revision changed"):
        resolver.capture_agent_sources(plan, **{**kw, "expected_source_sha256": "0" * 64})
    with pytest.raises(FleetPlanInvalid, match="original source-acquisition proof"):
        resolver.capture_agent_sources(
            plan,
            **{
                **kw,
                "inputs": {**kw["inputs"], "skill": "github:team/review@" + "a" * 40 + ":skill"},
            },
        )


def test_capture_identity_tracks_imported_goon_source_not_only_root_plan(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    first = resolver.capture_agent_sources(plan, **kw)
    goon_file = tmp_path / "goons" / "agent" / "capture" / "goon.yml"
    goon_file.write_text(
        goon_file.read_text().replace("echo not-executable", "echo changed-but-still-blocked")
    )
    # The exact root Plan file stays unchanged. Imported source must change the
    # commitment even though the same repository/skill bytes were captured.
    second = resolver.capture_agent_sources(plan, **kw)
    assert first.plan_source_sha256 == second.plan_source_sha256
    assert first.repository.content_identity == second.repository.content_identity
    assert first.plan_graph_identity != second.plan_graph_identity
    assert first.capture_identity != second.capture_identity
