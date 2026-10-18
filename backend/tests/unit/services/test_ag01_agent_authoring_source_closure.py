"""AG-01 source-closure boundaries: no agent effects, no second parser/launcher."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _setup(tmp_path: Path):
    goons = tmp_path / "goons"
    goon = goons / "agent" / "source-preview"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# source-preview\n", encoding="utf-8")
    (goon / "goon.yml").write_text("""inputs:
  repo:
    type: repository_reference
  skill:
    type: skill_reference
  acceptance:
    type: acceptance_profile
steps:
- run: echo preview-only
""", encoding="utf-8")
    plan = tmp_path / "plan.yml"
    plan.write_text("""inputs: [repo, skill, acceptance]
goons: [agent/source-preview]
""", encoding="utf-8")
    repositories = tmp_path / "approved-repositories"
    repo = repositories / "team" / "app"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    skills = tmp_path / "approved-skills"
    skill = skills / "reviews" / "semantic"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "assets").mkdir()
    (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")
    (skill / "scripts" / "check.sh").write_text("echo checked\n", encoding="utf-8")
    (skill / "references" / "RULES.md").write_text("# Rules\n", encoding="utf-8")
    (skill / "assets" / "spec.json").write_text("{}\n", encoding="utf-8")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goons))
    _, sha = resolver.input_definitions_with_source(plan)
    values = {
        "repo": "local:team/app",
        "skill": "local:reviews/semantic",
        "acceptance": "semantic-verifier",
    }
    options = {
        "repository_root": repositories,
        "skill_root": skills,
        "allowed_acceptance": ("semantic-verifier",),
        "expected_source_sha256": sha,
    }
    return resolver, plan, values, options, repo, skill


def test_canonical_resolver_readonly_explain_includes_full_skill_and_source_identity(tmp_path):
    resolver, plan, values, options, _, skill = _setup(tmp_path)
    report = resolver.explain_agent_sources(plan, inputs=values, **options)
    assert report["authority"] == "READ_ONLY_PREWORK_NOT_ADMITTED"
    assert report["transported_immutable_snapshot"] is False
    assert report["original_enforcer_admission"] == "NOT_ATTEMPTED"
    assert {entry["path"] for entry in report["skill"]["members"]} == {
        "SKILL.md", "references/RULES.md", "scripts/check.sh", "assets/spec.json"
    }
    assert report["skill"]["entry_count"] == 4
    assert not (skill / ".oh-goon-data").exists()
    assert report["egress_recipients"] == []
    # No executable route until AG-02 adds the original typed Step/admission.
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve(plan, inputs=values)


def test_same_skill_bytes_have_same_content_identity_at_distinct_authorized_origins(tmp_path):
    resolver, plan, values, options, _, skill = _setup(tmp_path)
    initial = resolver.explain_agent_sources(plan, inputs=values, **options)
    new_skill = options["skill_root"] / "team2" / "semantic"
    for original in skill.rglob("*"):
        relative = original.relative_to(skill)
        target = new_skill / relative
        if original.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
    values["skill"] = "local:team2/semantic"
    second = resolver.explain_agent_sources(plan, inputs=values, **options)
    assert initial["skill"]["content_identity"] == second["skill"]["content_identity"]
    assert initial["skill"]["reference"] != second["skill"]["reference"]
    (new_skill / "SKILL.md").write_text("# Different\n", encoding="utf-8")
    third = resolver.explain_agent_sources(plan, inputs=values, **options)
    assert third["skill"]["content_identity"] != initial["skill"]["content_identity"]


@pytest.mark.parametrize("override, match", [
    ({"repo": "local:../secrets"}, "invalid repository_reference"),
    ({"repo": "local:team/../secrets"}, "invalid repository_reference"),
    ({"repo": "local:/etc"}, "invalid repository_reference"),
    ({"skill": "github:team/skills@main:reviews/semantic"}, "invalid skill_reference"),
    ({"skill": "github:team/skills@" + "a"*40 + ":reviews/semantic"}, "original source-acquisition proof"),
    ({"acceptance": "not-authorized"}, "not explicitly authorized"),
])
def test_reject_invalid_or_unavailable_authority_before_effects(tmp_path, override, match):
    resolver, plan, values, options, *_ = _setup(tmp_path)
    values.update(override)
    with pytest.raises(FleetPlanInvalid, match=match):
        resolver.explain_agent_sources(plan, inputs=values, **options)


def test_reject_symlink_and_hardlink_skill_escape(tmp_path):
    resolver, plan, values, options, _, skill = _setup(tmp_path)
    (skill / "assets" / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(FleetPlanInvalid, match="non-regular"):
        resolver.explain_agent_sources(plan, inputs=values, **options)
    (skill / "assets" / "escape").unlink()
    external = tmp_path / "outside"
    external.write_text("top secret", encoding="utf-8")
    import os
    os.link(external, skill / "assets" / "hardlink")
    with pytest.raises(FleetPlanInvalid, match="hardlink"):
        resolver.explain_agent_sources(plan, inputs=values, **options)


def test_reject_missing_skill_and_stale_plan(tmp_path):
    resolver, plan, values, options, _, skill = _setup(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="revision changed"):
        resolver.explain_agent_sources(plan, inputs=values, **{**options, "expected_source_sha256": "0" * 64})
    (skill / "SKILL.md").unlink()
    with pytest.raises(FleetPlanInvalid, match="SKILL.md"):
        resolver.explain_agent_sources(plan, inputs=values, **options)


def test_reject_unbounded_and_duplicate_role_definitions(tmp_path):
    resolver, plan, values, options, repo, _ = _setup(tmp_path)
    (repo / "huge.bin").write_bytes(b"a" * (8 * 1024 * 1024 + 1))
    with pytest.raises(FleetPlanInvalid, match="byte budget"):
        resolver.explain_agent_sources(plan, inputs=values, **options)
    (repo / "huge.bin").unlink()
    goon = tmp_path / "goons" / "agent" / "source-preview" / "goon.yml"
    goon.write_text(goon.read_text().replace("steps:\n", "  repo2:\n    type: repository_reference\nsteps:\n"))
    plan.write_text(plan.read_text().replace("repo, skill, acceptance", "repo, repo2, skill, acceptance"))
    with pytest.raises(FleetPlanInvalid, match="duplicate repository_reference"):
        # Force reread the canonical graph by updating the SHA supplied.
        _, next_sha = resolver.input_definitions_with_source(plan)
        resolver.explain_agent_sources(plan, inputs={**values, "repo2": "local:team/app"}, **{**options, "expected_source_sha256": next_sha})


def test_existing_plan_inputs_cli_uses_pre_runtime_owner_without_new_agent_command(tmp_path, capsys, monkeypatch):
    from oh_goon import cli
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    root = tmp_path / "goon.yml"
    root.write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- run: echo preview-only
""", encoding="utf-8")
    repo_root = tmp_path / "repositories"
    repo = repo_root / "target"
    repo.mkdir(parents=True)
    (repo / "a.txt").write_text("hello\n")
    skill_root = tmp_path / "skills"
    skill = skill_root / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\n")
    _, digest = FleetPlanResolver().input_definitions_with_source(root)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("AG-01 must not create ApplicationRuntime or Run state")
    monkeypatch.setattr(cli, "_build_application_runtime", forbidden)
    args = ["plan", "inputs", str(root), "--source-closure", "--expect-sha256", digest,
            "--repository-root", str(repo_root), "--skill-root", str(skill_root),
            "--allow-acceptance", "semantic-verifier", "--input", "repo=local:target",
            "--input", "skill=local:review", "--input", "acceptance=semantic-verifier"]
    assert cli.main(args) == 0
    import json
    report = json.loads(capsys.readouterr().out)
    assert report["original_enforcer_admission"] == "NOT_ATTEMPTED"
    assert report["skill"]["members"][0]["path"] == "SKILL.md"
    assert cli.main(args[:-2]) == 2  # missing acceptance fails before any effects


def test_team_skill_alias_is_explicitly_frozen_and_cannot_chain_or_expand_authority(tmp_path):
    resolver, plan, values, options, _, _ = _setup(tmp_path)
    original = resolver.explain_agent_sources(plan, inputs=values, **options)
    values["skill"] = "semantic-review"
    with pytest.raises(FleetPlanInvalid, match="no explicit frozen expansion"):
        resolver.explain_agent_sources(plan, inputs=values, **options)
    aliased = resolver.explain_agent_sources(
        plan, inputs=values, skill_aliases={"semantic-review": "local:reviews/semantic"}, **options
    )
    assert aliased["authored_skill_reference"] == "semantic-review"
    assert aliased["skill"]["reference"] == "local:reviews/semantic"
    assert aliased["skill"]["content_identity"] == original["skill"]["content_identity"]
    for invalid in ("other-skill", "local:../outside", "github:team/pkg@main:path"):
        with pytest.raises(FleetPlanInvalid, match="alias"):
            resolver.explain_agent_sources(
                plan, inputs=values, skill_aliases={"semantic-review": invalid}, **options
            )


def test_nested_nonprojected_agent_inputs_cannot_bypass_original_execution_fence(tmp_path):
    resolver, plan, values, options, _, _ = _setup(tmp_path)
    goon = tmp_path / "goons" / "agent" / "source-preview" / "goon.yml"
    goon.write_text("""inputs:
  repo: {type: repository_reference, default: 'local:team/app'}
  skill: {type: skill_reference, default: 'local:reviews/semantic'}
  acceptance: {type: acceptance_profile, default: semantic-verifier}
steps:
- run: echo unreachable
""", encoding="utf-8")
    plan.write_text("goons: [agent/source-preview]\n", encoding="utf-8")
    # No projected root input can conceal this nested agent source type.
    with pytest.raises(FleetPlanInvalid, match="preview-only"):
        resolver.resolve(plan)
