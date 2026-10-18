"""SKILL-2: committed Gitlink pin and portable original prework bytes, offline."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.agent_skill_registry import read_goonskills
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                           "-c", "protocol.file.allow=always", *args], cwd=repo,
                          check=True, capture_output=True, text=True, timeout=10)
    return proc.stdout.strip()


def setup(tmp_path: Path):
    remote = tmp_path / "fixture-skill-repo"
    remote.mkdir()
    git(remote, "init", "-q")
    review = remote / "review"
    (review / "references").mkdir(parents=True)
    (review / "scripts").mkdir()
    (review / "assets").mkdir()
    (review / "SKILL.md").write_text("# Pinned review\n")
    (review / "references" / "checklist.md").write_text("# Checks\n")
    (review / "scripts" / "review.sh").write_text("echo review\n")
    (review / "assets" / "policy.json").write_text('{"strict": true}\n')
    git(remote, "add", ".")
    git(remote, "commit", "-qm", "skill fixture")
    revision = git(remote, "rev-parse", "HEAD")

    root = tmp_path / "skill-root"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "submodule", "add", "-q", str(remote), "libs/agent-skills")
    git(root, "add", ".gitmodules", "libs/agent-skills")
    git(root, "commit", "-qm", "pinned gitlink")

    goon = tmp_path / "goons" / "agent" / "review"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# Agent\n")
    (goon / "goon.yml").write_text('''inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: opencode
  task: review the repository
''')
    plan = tmp_path / "plan.yml"
    plan.write_text('inputs: [repo, skill, acceptance]\ngoons: [agent/review]\n')
    registry = tmp_path / ".goonskills"
    registry.write_text(f'[skill "team/review"]\nsource = gitlink\npath = libs/agent-skills/review\ngitlink = libs/agent-skills\nrevision = {revision}\n')
    repos = tmp_path / "repositories"
    (repos / "product").mkdir(parents=True)
    (repos / "product" / "app.py").write_text("print('hi')\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, sha = resolver.input_definitions_with_source(plan)
    inputs = {"repo": "local:product", "skill": "team/review", "acceptance": "review"}
    kwargs = dict(repository_root=repos, skill_root=root,
                  allowed_acceptance=("review",), expected_source_sha256=sha)
    return resolver, plan, registry, root, remote, revision, inputs, kwargs


def test_pin_accepts_full_source_once_and_offline_handoff_round_trip(tmp_path):
    resolver, plan, registry, root, _, revision, inputs, kwargs = setup(tmp_path)
    parsed = read_goonskills(registry)
    assert parsed.pins["team/review"].revision == revision
    preview = resolver.explain_agent_sources(plan, inputs=inputs, **kwargs)
    assert preview["skill"]["reference"] == f"gitlink:{revision}:libs/agent-skills/review"
    assert preview["skill"]["source_origin"] == "COMMITTED_GITLINK_OFFLINE_CAPTURE"
    assert {m["path"] for m in preview["skill"]["members"]} == {
        "SKILL.md", "references/checklist.md", "scripts/review.sh", "assets/policy.json",
    }
    capture = resolver.capture_agent_sources(plan, inputs=inputs, **kwargs)
    capture.verify()
    assert capture.resolved_skill_reference == preview["skill"]["reference"]
    handoff = resolver.prepare_agent_step_handoff(plan, inputs=inputs, **kwargs)
    packet = handoff.encode_prework_delivery()
    # Offline decoding cannot reacquire Git or source files. The original
    # transport includes every resource and authenticates the frozen bytes.
    gitlink = root / "libs" / "agent-skills"
    renamed = root / "libs" / "offline"
    gitlink.rename(renamed)
    restored = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=handoff.handoff_identity,
    )
    assert restored.encode_prework_delivery() == packet
    assert restored.source.skill.content_identity == capture.skill.content_identity
    assert {m.path for m in restored.source.skill.members} == {
        "SKILL.md", "references/checklist.md", "scripts/review.sh", "assets/policy.json",
    }
    assert restored.effect_permission == "NONE"
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve(plan, inputs=inputs)


@pytest.mark.parametrize("mutation", ["changed", "untracked", "missing", "wrong-pin", "wrong-link", "stale-head"])
def test_pin_refuses_changed_untracked_or_uncommitted_sources(tmp_path, mutation):
    resolver, plan, registry, root, remote, revision, inputs, kwargs = setup(tmp_path)
    checkout = root / "libs" / "agent-skills"
    if mutation == "changed":
        (checkout / "review" / "SKILL.md").write_text("# changed\n")
    elif mutation == "untracked":
        (checkout / "review" / "new.md").write_text("untracked\n")
    elif mutation == "missing":
        (checkout / "review" / "SKILL.md").unlink()
    elif mutation == "wrong-pin":
        registry.write_text(registry.read_text().replace(revision, "a" * 40))
    elif mutation == "wrong-link":
        registry.write_text(registry.read_text().replace("gitlink = libs/agent-skills", "gitlink = libs/another"))
    elif mutation == "stale-head":
        (remote / "review" / "SKILL.md").write_text("# next revision\n")
        git(remote, "add", ".")
        git(remote, "commit", "-qm", "next")
        git(checkout, "fetch", "-q", str(remote))
        git(checkout, "checkout", "-q", git(remote, "rev-parse", "HEAD"))
    with pytest.raises(FleetPlanInvalid, match="SKILL-2"):
        resolver.capture_agent_sources(plan, inputs=inputs, **kwargs)


def test_gitlink_rejects_unpinned_and_ambiguous_registry_fields(tmp_path):
    resolver, plan, registry, _, _, revision, inputs, kwargs = setup(tmp_path)
    original = registry.read_text()
    for invalid in (
        original.replace("revision = " + revision, "revision = main"),
        original.replace("revision = " + revision + "\n", ""),
        original.replace("gitlink = libs/agent-skills", "gitlink = ../unsafe"),
        original.replace("gitlink = libs/agent-skills", "gitlink = libs/agent-skills-2"),
        original.replace("source = gitlink", "source = github"),
        original + "unknown = anything\n",
    ):
        registry.write_text(invalid)
        with pytest.raises(FleetPlanInvalid, match="SKILL"):
            resolver.explain_agent_sources(plan, inputs=inputs, **kwargs)
    registry.write_text(original)
    with pytest.raises(FleetPlanInvalid, match="cannot be mixed"):
        resolver.explain_agent_sources(plan, inputs=inputs, skill_aliases={"team/review": "local:else"}, **kwargs)


def test_offline_template_relocation_preserves_complete_skill_bytes(tmp_path):
    import shutil

    template = Path(__file__).resolve().parents[4] / "examples" / "agent-skill-offline"
    # Source trees vary in packaging layouts; package-relative root is known
    # from the checkout and verified before testing its portable template.
    assert template.is_dir()
    options = []
    for target in (tmp_path / "location-a", tmp_path / "location-b"):
        shutil.copytree(template, target)
        repos = target / "approved-repos" / "product"
        repos.mkdir(parents=True)
        (repos / "main.py").write_text("print('offline')\n")
        plan = target / "goon.yml"
        resolver = FleetPlanResolver()
        _, sha = resolver.input_definitions_with_source(plan)
        values = {"repo": "local:product", "skill": "team/review", "acceptance": "review"}
        kwargs = dict(repository_root=target / "approved-repos", skill_root=target,
                      allowed_acceptance=("review",), expected_source_sha256=sha)
        preview = resolver.explain_agent_sources(plan, inputs=values, **kwargs)
        assert preview["skill"]["reference"] == "local:skills/team/review"
        assert {m["path"] for m in preview["skill"]["members"]} == {
            "SKILL.md", "references/checklist.md", "scripts/verify.sh", "assets/rules.json"
        }
        options.append(preview["skill"]["content_identity"])
        captured = resolver.capture_agent_sources(plan, inputs=values, **kwargs)
        assert captured.skill.content_identity == preview["skill"]["content_identity"]
        with pytest.raises(FleetPlanInvalid):
            resolver.resolve(plan, inputs=values)
    assert options[0] == options[1]


def test_git_blob_proof_refuses_mutation_even_if_status_is_untrustworthy(tmp_path, monkeypatch):
    import oh_goon.services.agent_skill_registry as registry_module

    resolver, plan, _, root, _, _, inputs, kwargs = setup(tmp_path)
    skill = root / "libs" / "agent-skills" / "review" / "SKILL.md"
    skill.write_text("# malicious changed source\n")
    real_git = registry_module._git

    def fake_status(cwd, *args, **kw):
        if args and args[0] == "status":
            return b""
        return real_git(cwd, *args, **kw)

    monkeypatch.setattr(registry_module, "_git", fake_status)
    with pytest.raises(FleetPlanInvalid, match="captured resources differ"):
        resolver.capture_agent_sources(plan, inputs=inputs, **kwargs)
