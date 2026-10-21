"""Local synthetic Skills and source for native review/repair contract tests."""

from pathlib import Path
from shutil import copytree

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
    ResolvedNativeAgent,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import PLAN_TEMPLATES


def agent_review_case(
    tmp_path: Path,
    *,
    endpoint="http://127.0.0.1:12345",
    plan_template="agents/agentic-review-repair-loop",
    native_agent: ResolvedNativeAgent | None = None,
):
    names = (
        "single-source-of-truth-review",
        "semantic-redecision-review",
        "resolved-fact-regression-review",
        "canonical-consumer-contract-review",
    )
    repo = tmp_path / "repos/repo"
    repo.mkdir(parents=True)
    (repo / "code.txt").write_text("broken\n")
    (repo / "check").write_text(
        '#!/bin/sh\nif [ -f regression.txt ]; then /bin/sh regression.txt; else [ "$(cat code.txt)" = repaired ]; fi\n'
    )
    (repo / "check").chmod(0o755)
    for name in (*names, "adjudicate", "repair"):
        directory = tmp_path / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "# Synthetic fixture\nInspect code.txt using the native typed record contract.\n"
        )
    registry = "local:\n" + "".join(f"  cookbook/{name}: skills/{name}\n" for name in names)
    (tmp_path / ".goonskills.yml").write_text(registry)
    plan_path = tmp_path / "plan.yml"
    plan_path.write_text(PLAN_TEMPLATES.source(plan_template).content)
    connection = AgentConnection.select(
        AgentProvidersConfig(
            default_model="review",
            providers={"test": AgentProvider(api="openai-chat-completions", base_url=endpoint)},
            models={
                "review": AgentModel(
                    provider="test", model="synthetic/reviewer", max_output_tokens=8192
                )
            },
        )
    )
    templates = GoonTemplateStore()
    if native_agent is not None:
        native_templates = tmp_path / "native-goons"
        copytree(templates.root / "agents", native_templates / "agents")
        for name, harness, role in (
            ("review", "provider-review", "review"),
            ("adjudicate", "provider-adjudicate", "adjudication"),
            ("repair", "provider-proposal", "proposal"),
        ):
            source = native_templates / "agents" / name / "goon.yml"
            recipients = "[configured_model]" if native_agent.connection is not None else "[]"
            source.write_text(
                source.read_text()
                .replace(f"harness: {harness}", f"harness: native\n  role: {role}")
                .replace("recipients: [configured_model]", f"recipients: {recipients}")
            )
        templates = GoonTemplateStore(native_templates)
    resolver = FleetPlanResolver(goon_templates=templates)
    options = dict(
        inputs={
            "repo": "local:repo",
            "regression_command": "check",
            "verification_command": "check",
        },
        repository_root=tmp_path / "repos",
        skill_root=tmp_path,
        allowed_acceptance=("agentic-review",),
        connection=connection,
    )
    if native_agent is not None:
        options.pop("connection")
        options["native_agent"] = native_agent
        options["repository"] = repo
    return resolver, plan_path, repo, options
