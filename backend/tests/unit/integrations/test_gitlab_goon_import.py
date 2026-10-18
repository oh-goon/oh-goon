from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.core.models.fleet_plan import ExecutionPurpose, SerialRepositoryExecution
from oh_goon.core.models.fleet_plan_migration import GitLabResolvedComponent
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template, parse_plan_template
from oh_goon.integrations.gitlab_ci_component import (
    GitLabComponentFetcher,
    GitLabComponentSource,
    parse_component_reference,
)
from oh_goon.integrations.gitlab_ci_goon import GitLabCiGoonMigrator

_COMPONENTS = {
    "gitlab.internal/cicd-catalog/standard-pipeline@4.2.0": """spec:\n  inputs:\n    stage:\n      default: verify\n---\nprecommit:\n  stage: $[[ inputs.stage ]]\n  timeout: 10 minutes\n  script: pre-commit run --all-files\n""",
    "gitlab.internal/cicd-catalog/docker-tag@4.2.0": """spec:\n  inputs:\n    image:\n      default: app:dev\n---\ndocker-tag:\n  stage: package\n  timeout: 60 minutes\n  script: docker tag $[[ inputs.image ]] harbor.internal/team/app:dev\n""",
    "gitlab.internal/ai-cicd-catalog/standard-python@8.1.0": """spec:\n  inputs:\n    enable_ai_cicd_uv:\n      type: boolean\n      default: true\n    default_image:\n      default: python:3.13\n---\npython-check:\n  stage: verify\n  timeout: 60 minutes\n  script: echo uv=$[[ inputs.enable_ai_cicd_uv ]] image=$[[ inputs.default_image ]]\n""",
}


class _Fetcher:
    def __init__(self) -> None:
        self.references: list[str] = []

    def fetch(self, reference: str, *, token: str | None = None) -> GitLabComponentSource:
        del token
        self.references.append(reference)
        content = _COMPONENTS[reference]
        host, project, component, revision = parse_component_reference(reference)
        return GitLabComponentSource(
            metadata=GitLabResolvedComponent(
                reference=reference,
                host=host,
                project=project,
                component=component,
                revision=revision,
                source_path=f"templates/{component}.yml",
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            ),
            content=content,
        )


def test_import_gitlab_components_becomes_native_atomic_goon_yaml(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [precommit, verify, package]\ninclude:\n  - component: ${CI_SERVER_HOST}/cicd-catalog/standard-pipeline@${COMPONENT_VERSION}\n    inputs:\n      stage: precommit\n  - component: ${CI_SERVER_HOST}/cicd-catalog/docker-tag@${COMPONENT_VERSION}\n  - component: ${CI_SERVER_HOST}/ai-cicd-catalog/standard-python@${AI_COMPONENT_VERSION}\n    inputs:\n      enable_ai_cicd_uv: \"false\"\n      default_image: \"my_custom_image:latest\"\n""",
        encoding="utf-8",
    )
    fetcher = _Fetcher()
    migrator = GitLabCiGoonMigrator(fetcher)  # type: ignore[arg-type]

    migration = migrator.migrate(
        source,
        import_variables={
            "CI_SERVER_HOST": "gitlab.internal",
            "COMPONENT_VERSION": "4.2.0",
            "AI_COMPONENT_VERSION": "8.1.0",
        },
    )

    assert migration.ready is True
    assert migration.goon.execution.purpose is ExecutionPurpose.CICD
    assert isinstance(migration.goon.execution.repositories, SerialRepositoryExecution)
    assert fetcher.references == list(_COMPONENTS)
    assert [step.kind for step in migration.goon.steps] == ["pre_commit", "command", "command"]
    assert migration.goon.steps[1].argv == ("echo", "uv=false", "image=my_custom_image:latest")  # type: ignore[attr-defined]
    assert migration.goon.steps[-1].argv[0:2] == ("docker", "tag")  # type: ignore[attr-defined]
    assert migration.issues == ()

    output = tmp_path / "goon.yml"
    migrator.write_goon(output, migration)
    rendered = output.read_text(encoding="utf-8")
    assert "purpose: cicd" not in rendered
    assert "kind: git_repository" not in rendered
    assert "admission:" not in rendered
    assert "threat_model:" not in rendered
    assert "cpu_millis:" not in rendered
    assert "memory_mib:" not in rendered
    assert "mode: serial" not in rendered
    assert "uses: command" not in rendered
    assert "step_id:" not in rendered
    assert "run:" in rendered
    assert "include:" not in rendered
    assert "component:" not in rendered
    assert "GitLab is not consulted when this Goon runs" in rendered
    assert "${CI_SERVER_HOST}" not in rendered
    imported = parse_goon_template(rendered, source=str(output))
    assert imported.execution is not None
    assert imported.execution.purpose is ExecutionPurpose.CICD
    assert len(imported.steps) == 3
    with pytest.raises(FleetPlanInvalid, match="Plans cannot declare executable work"):
        parse_plan_template(rendered, source=str(output))


def test_import_gitlab_output_uses_source_cas(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text("job:\n  timeout: 60 minutes\n  script: echo ok\n", encoding="utf-8")
    migrator = GitLabCiGoonMigrator(GitLabComponentFetcher())
    first = migrator.migrate(source)
    output = tmp_path / "goon.yml"

    committed = migrator.write_goon(output, first)
    first_bytes = output.read_bytes()
    assert committed.revision == hashlib.sha256(first_bytes).hexdigest()

    source.write_text("job:\n  timeout: 60 minutes\n  script: echo changed\n", encoding="utf-8")
    second = migrator.migrate(source)
    with pytest.raises(FleetPlanSourceStale, match="changed before save"):
        migrator.write_goon(output, second)
    assert output.read_bytes() == first_bytes

    replaced = migrator.write_goon(
        output,
        second,
        expected_revision=committed.revision,
    )
    assert replaced.revision == hashlib.sha256(output.read_bytes()).hexdigest()
    assert replaced.revision != committed.revision
    rendered = output.read_text(encoding="utf-8")
    assert "name:" not in rendered
    assert "description:" not in rendered
    assert "echo changed" in rendered


def test_import_gitlab_blocks_output_for_runtime_semantics(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """integration:\n  image: python:3.13\n  services: [postgres:17]\n  script: uv run pytest tests/integration\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    assert {issue.field for issue in migration.issues} >= {"image", "services"}
    with pytest.raises(FleetPlanInvalid, match="unsupported semantics"):
        GitLabCiGoonMigrator.write_goon(tmp_path / "fleet.yml", migration)


def test_component_reference_rejects_moving_selector() -> None:
    with pytest.raises(FleetPlanInvalid):
        parse_component_reference("gitlab.internal/team/catalog/build@~latest")


@pytest.mark.parametrize(
    ("body", "blocking_fields"),
    [
        ("job:\n  script: [uv run pytest, 7]\nfallback:\n  script: echo ok\n", {"script"}),
        ("job:\n  script: uv run pytest\n  before_script: 7\n", {"before_script"}),
        ("job:\n  script: uv run pytest\n  after_script: echo done\n", {"after_script"}),
        ("job:\n  script: uv run pytest\n  retry: 2\n", {"retry"}),
        ("job:\n  script: uv run pytest\n  parallel: 2\n", {"parallel"}),
        ("job:\n  script: uv run pytest\n  artifacts: later\n", {"artifacts"}),
        ("job:\n  script: uv run pytest\n  artifacts:\n    paths: dist/*\n", {"artifacts.paths"}),
        ("job:\n  script: uv run pytest\n  timeout: forever\n", {"timeout"}),
        ("job:\n  script: uv run pytest\n  variables: [A, B]\n", {"variables"}),
    ],
)
def test_gitlab_import_reports_unsupported_job_shapes(
    tmp_path: Path, body: str, blocking_fields: set[str]
) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(body, encoding="utf-8")
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    assert {issue.field for issue in migration.issues} >= blocking_fields


def test_gitlab_import_compiles_exact_single_command_git_artifacts_and_timeout(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [verify, package]\nprepare:\n  stage: verify\n  timeout: 2 minutes\n  script: git status --short\nbuild:\n  stage: package\n  needs: [prepare]\n  timeout: 60 minutes\n  artifacts:\n    paths: [dist/*]\n  script: uv build\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is True
    assert migration.issues == ()
    assert [step.kind for step in migration.goon.steps] == ["git", "command"]
    git_step = migration.goon.steps[0]
    assert git_step.action.args == ("status", "--short")  # type: ignore[attr-defined]
    assert migration.goon.steps[-1].timeout_seconds == 3600.0
    assert tuple(item.path for item in migration.goon.steps[-1].artifacts) == ("dist/*",)



def test_gitlab_import_blocks_nondefault_command_timeout(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        "job:\n  timeout: 10 minutes\n  script: uv run pytest\n",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    assert any(
        issue.field == "timeout" and "cannot be authored on a normal Fleet run: Step" in issue.reason
        for issue in migration.issues
    )

def test_gitlab_import_blocks_shell_context_variables_and_multi_command_jobs(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [setup, build, publish]\nsetup:\n  stage: setup\n  timeout: 2 minutes\n  before_script: cd src\n  script: uv run pytest\nbuild:\n  stage: build\n  timeout: 2 minutes\n  variables:\n    LOCAL: true\n  script: echo $RELEASE_ID\npublish:\n  stage: publish\n  timeout: 2 minutes\n  script:\n    - echo first\n    - echo second\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    fields = {issue.field for issue in migration.issues}
    assert {"before_script", "variables", "script.$RELEASE_ID", "script"} <= fields


def test_gitlab_import_blocks_ambient_global_variables(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """variables:\n  CHANNEL: stable\njob:\n  timeout: 2 minutes\n  script: uv run pytest\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    assert any(
        issue.field == "variables" and "ambient job exports" in issue.reason
        for issue in migration.issues
    )


def test_gitlab_import_reports_bad_needs_and_cycle(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """a:\n  needs: [b]\n  script: echo a\nb:\n  needs:\n    - job: a\n      optional: true\n  script: echo b\nc:\n  needs: missing\n  script: echo c\nd:\n  needs:\n    - project: other/project\n      job: build\n  script: echo d\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    fields = [issue.field for issue in migration.issues]
    assert fields.count("needs") >= 4
    assert migration.ready is False


def test_gitlab_import_reports_global_merge_and_variable_problems(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: broken\nvariables:\n  BAD-NAME: value\n  META:\n    value: x\nworkflow: {rules: []}\ndefault: {retry: 2}\n.hidden:\n  script: echo hidden\njob:\n  script: echo ok\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    fields = {issue.field for issue in migration.issues}
    assert {"stages", "workflow", "default", "hidden-job", "variables"} <= fields


def test_gitlab_import_blocks_unresolved_runner_shell_semantics(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [shell, quoted]\nshell:\n  stage: shell\n  timeout: 10 minutes\n  script: echo $TOKEN && echo done\nquoted:\n  stage: quoted\n  timeout: 10 minutes\n  script: "echo 'unterminated"\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    fields = {issue.field for issue in migration.issues}
    assert {"script.$TOKEN", "script.shell"} <= fields


def test_gitlab_import_never_truncates_source_timeouts(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [long, git]\nlong:\n  stage: long\n  timeout: 7 hours\n  script: uv run pytest\ngit-long:\n  stage: git\n  timeout: 2 hours\n  script: git status --short\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    timeout_reasons = [issue.reason for issue in migration.issues if issue.field == "timeout"]
    assert any("21600" in reason and "truncating" in reason for reason in timeout_reasons)
    assert any("3600" in reason and "truncating" in reason for reason in timeout_reasons)


def test_gitlab_import_is_closed_world_for_job_and_top_level_fields(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """custom_global: enabled\njob:\n  timeout: 10 minutes\n  allow_failure: true\n  script: uv run pytest\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    fields = {issue.field for issue in migration.issues}
    assert {"custom_global", "allow_failure"} <= fields


def test_gitlab_import_blocks_concurrency_instead_of_serializing_it(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [test]\na:\n  stage: test\n  timeout: 10 minutes\n  script: echo a\nb:\n  stage: test\n  timeout: 10 minutes\n  script: echo b\n""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    assert any(issue.field == "jobs" and "concurrent" in issue.reason for issue in migration.issues)


def test_component_input_contract_errors_are_reported_as_blocking_include(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """include:\n  - component: gitlab.internal/team/catalog/build@1.2.3\n    inputs:\n      unknown: value\nfallback:\n  script: echo ok\n""",
        encoding="utf-8",
    )
    content = """spec:\n  inputs:\n    enabled:\n      type: boolean\n      default: true\n---\nbuild:\n  script: echo $[[ inputs.enabled ]]\n"""

    class Fetcher:
        def fetch(self, reference: str, *, token: str | None = None) -> GitLabComponentSource:
            del token
            host, project, component, revision = parse_component_reference(reference)
            return GitLabComponentSource(
                metadata=GitLabResolvedComponent(
                    reference=reference,
                    host=host,
                    project=project,
                    component=component,
                    revision=revision,
                    source_path="templates/build.yml",
                    content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                ),
                content=content,
            )

    migration = GitLabCiGoonMigrator(Fetcher()).migrate(source)  # type: ignore[arg-type]
    assert migration.ready is False
    assert any("unknown inputs" in issue.reason for issue in migration.issues)
    assert len(migration.goon.steps) == 1


def test_gitlab_import_does_not_invent_project_timeout_or_docker_daemon_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """build:
  script: docker build .
""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    assert migration.ready is False
    assert any(issue.field == "timeout" for issue in migration.issues)
