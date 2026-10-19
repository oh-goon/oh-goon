"""Local Skill resources remain portable with their workflow."""

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


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
        kwargs = dict(
            repository_root=target / "approved-repos",
            skill_root=target,
            allowed_acceptance=("review",),
            expected_source_sha256=sha,
        )
        preview = resolver.explain_agent_sources(plan, inputs=values, **kwargs)
        assert preview["skill"]["reference"] == "local:skills/team/review"
        assert {m["path"] for m in preview["skill"]["members"]} == {
            "SKILL.md",
            "references/checklist.md",
            "scripts/verify.sh",
            "assets/rules.json",
        }
        options.append(preview["skill"]["content_identity"])
        captured = resolver.capture_agent_sources(plan, inputs=values, **kwargs)
        assert captured.skill.content_identity == preview["skill"]["content_identity"]
        with pytest.raises(FleetPlanInvalid):
            resolver.resolve(plan, inputs=values)
    assert options[0] == options[1]
