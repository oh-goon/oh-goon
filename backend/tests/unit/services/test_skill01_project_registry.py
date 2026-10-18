"""SKILL-0/1: one registry feeds original AG-01 capture; no Agent execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.agent_skill_registry import read_goonskills
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def fixture(tmp_path: Path):
    (tmp_path / "goons" / "agent" / "review").mkdir(parents=True)
    (tmp_path / "goons" / "agent" / "review" / "README.md").write_text("# Review\n")
    (tmp_path / "goons" / "agent" / "review" / "goon.yml").write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: opencode
  task: Review repository
""")
    plan = tmp_path / "plan.yml"
    plan.write_text("inputs: [repo, skill, acceptance]\ngoons: [agent/review]\n")
    repo_root = tmp_path / "repos"
    (repo_root / "project").mkdir(parents=True)
    (repo_root / "project" / "x.py").write_text("print(1)\n")
    skill_root = tmp_path / "skills"
    (skill_root / "review").mkdir(parents=True)
    (skill_root / "review" / "SKILL.md").write_text("# Review\n")
    (skill_root / "review" / "references").mkdir()
    (skill_root / "review" / "references" / "guide.md").write_text("a reference\n")
    registry = tmp_path / ".goonskills.yml"
    registry.write_text("local: {review: review}\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, sha = resolver.input_definitions_with_source(plan)
    values = {"repo": "local:project", "skill": "review", "acceptance": "semantic-verifier"}
    opts = dict(
        repository_root=repo_root,
        skill_root=skill_root,
        allowed_acceptance=("semantic-verifier",),
        expected_source_sha256=sha,
    )
    return resolver, plan, registry, values, opts


def test_named_registry_uses_original_explain_and_full_capture(tmp_path):
    resolver, plan, registry, values, opts = fixture(tmp_path)
    parsed = read_goonskills(registry)
    assert dict(parsed.aliases) == {"review": "local:review"}
    preview = resolver.explain_agent_sources(plan, inputs=values, **opts)
    assert preview["authored_skill_reference"] == "review"
    assert preview["skill"]["reference"] == "local:review"
    assert {m["path"] for m in preview["skill"]["members"]} == {"SKILL.md", "references/guide.md"}
    assert preview["capability_authority"] == "NONE"
    capture = resolver.capture_agent_sources(plan, inputs=values, **opts)
    capture.verify()
    assert capture.authored_skill_reference == "review"
    assert capture.resolved_skill_reference == "local:review"
    assert capture.skill.content_identity == preview["skill"]["content_identity"]
    assert {m.path for m in capture.skill.members} == {"SKILL.md", "references/guide.md"}
    handoff = resolver.prepare_agent_step_handoff(plan, inputs=values, **opts)
    handoff.verify()
    assert handoff.source.capture_identity == capture.capture_identity
    assert handoff.effect_permission == "NONE"
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve(plan, inputs=values)


def test_registry_change_cannot_reuse_selected_source_identity(tmp_path):
    resolver, plan, registry, values, opts = fixture(tmp_path)
    before = resolver.capture_agent_sources(plan, inputs=values, **opts)
    (opts["skill_root"] / "review2").mkdir()
    (opts["skill_root"] / "review2" / "SKILL.md").write_text("# Other\n")
    registry.write_text("local: {review: review2}\n")
    after = resolver.capture_agent_sources(plan, inputs=values, **opts)
    assert before.capture_identity != after.capture_identity
    assert after.resolved_skill_reference == "local:review2"


def test_explicit_alias_and_project_registry_are_mutually_exclusive(tmp_path):
    resolver, plan, registry, values, opts = fixture(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="cannot be mixed"):
        resolver.explain_agent_sources(
            plan, inputs=values, skill_aliases={"review": "local:review"}, **opts
        )
    registry.unlink()
    with pytest.raises(FleetPlanInvalid, match="no explicit frozen expansion"):
        resolver.explain_agent_sources(plan, inputs=values, **opts)
    report = resolver.explain_agent_sources(
        plan, inputs=values, skill_aliases={"review": "local:review"}, **opts
    )
    assert report["skill"]["reference"] == "local:review"
    values["skill"] = "local:review"
    assert (
        resolver.explain_agent_sources(plan, inputs=values, **opts)["skill"]["reference"]
        == "local:review"
    )


@pytest.mark.parametrize(
    "body",
    [
        "local: {Review: review}\n",
        "local: {review: ../outside}\n",
        "local: {review: /tmp/outside}\n",
        'local: {review: "review\\other"}\n',
        "local: {review: .secret}\n",
        "local: {review: review//other}\n",
        "local: {review: review, review: other}\n",
        "unknown: {review: review}\n",
        "local: [review]\n",
        "local: {}\n",
        'sources: {team: {source: "https://example.test/skills.git", ref: main, skills: [../review]}}\n',
        'sources: {team: {source: "https://example.test/skills.git", skills: [review]}}\n',
        'sources: {team: {source: "https://example.test/skills.git", ref: main, skills: [a/review, b/review]}}\n',
        'sources: {team: {source: "https://example.test/skills.git", ref: main, skills: [review], unknown: true}}\n',
        'local: {team/review: review}\nsources: {team: {source: "https://example.test/skills.git", ref: main, skills: [review]}}\n',
    ],
)
def test_registry_invalid_fails_before_skill_capture(tmp_path, body, monkeypatch):
    resolver, plan, registry, values, opts = fixture(tmp_path)
    registry.write_text(body)

    def never_read(*args, **kwargs):
        raise AssertionError("invalid registry reached skill capture")

    monkeypatch.setattr(
        "oh_goon.services.agent_authoring_sources._snapshot_authorized_tree", never_read
    )
    with pytest.raises(FleetPlanInvalid, match="[Ss]kill|YAML"):
        resolver.explain_agent_sources(plan, inputs=values, **opts)


def test_registry_symlink_and_oversize_are_refused(tmp_path):
    resolver, plan, registry, values, opts = fixture(tmp_path)
    outside = tmp_path / "outside"
    registry.rename(outside)
    registry.symlink_to(outside)
    with pytest.raises(FleetPlanInvalid, match="[Ss]kill|YAML"):
        resolver.explain_agent_sources(plan, inputs=values, **opts)
    registry.unlink()
    registry.write_text("x" * 65537)
    with pytest.raises(FleetPlanInvalid, match="[Ss]kill|YAML"):
        resolver.explain_agent_sources(plan, inputs=values, **opts)


def test_bad_registry_does_not_change_normal_plan_resolution(tmp_path):
    resolver, plan, registry, values, opts = fixture(tmp_path)
    registry.write_text("garbage registry\n")
    with pytest.raises(FleetPlanInvalid, match="[Ss]kill|YAML"):
        resolver.explain_agent_sources(plan, inputs=values, **opts)
    # The typed Agent Step still has no executable route; registry cannot admit it.
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve(plan, inputs=values)


def test_existing_plan_inputs_cli_automatically_uses_goonskills_without_runtime(
    tmp_path, capsys, monkeypatch
):
    """No extra skill CLI or parallel resolver; CLI keeps original source projection."""
    import json

    from oh_goon import cli
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    plan = tmp_path / "goon.yml"
    plan.write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: opencode
  task: Review repository
""")
    (tmp_path / ".goonskills.yml").write_text("local: {team/review: team/review}\n")
    repo = tmp_path / "repos" / "my-project"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("print(1)\n")
    skill = tmp_path / "skills" / "team" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\n")
    _, sha = FleetPlanResolver().input_definitions_with_source(plan)

    def no_runtime(*args, **kwargs):
        raise AssertionError("source-closure must not construct runtime")

    monkeypatch.setattr(cli, "_build_application_runtime", no_runtime)
    args = [
        "plan",
        "inputs",
        str(plan),
        "--source-closure",
        "--expect-sha256",
        sha,
        "--repository-root",
        str(tmp_path / "repos"),
        "--skill-root",
        str(tmp_path / "skills"),
        "--allow-acceptance",
        "review",
        "--input",
        "repo=local:my-project",
        "--input",
        "skill=team/review",
        "--input",
        "acceptance=review",
    ]
    assert cli.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["authored_skill_reference"] == "team/review"
    assert report["skill"]["reference"] == "local:team/review"
    assert report["original_enforcer_admission"] == "NOT_ATTEMPTED"
    assert cli.main(args + ["--skill-alias", "team/review=local:team/review"]) == 2
    assert "cannot be mixed" in capsys.readouterr().out
