from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from oh_goon.cli_runtime_goon_import import _named_values, import_gitlab
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import CommandPlanStep, CompiledExecutionTemplate
from oh_goon.core.models.fleet_plan_migration import GitLabGoonMigration, GitLabImportIssue
from oh_goon.integrations.gitlab_ci_component import (
    GitLabComponentFetcher,
    GitLabComponentTransportError,
    parse_component_reference,
)


def _migration(*, ready: bool = True) -> GitLabGoonMigration:
    issues = () if ready else (GitLabImportIssue(job="build", field="image", reason="unsupported"),)
    return GitLabGoonMigration(
        source=".gitlab-ci.yml",
        goon=CompiledExecutionTemplate(
            name="imported",
            steps=(CommandPlanStep(timeout_seconds=3600, step_id="test", argv=("echo", "ok")),),
        ),
        issues=issues,
    )


def test_named_values_parses_and_rejects_invalid_values() -> None:
    assert _named_values(["A=1", "B=two=parts"], label="--variable") == {"A": "1", "B": "two=parts"}
    with pytest.raises(ValueError, match="NAME=VALUE"):
        _named_values(["BROKEN"], label="--variable")
    with pytest.raises(ValueError, match="unique non-empty"):
        _named_values(["=x"], label="--variable")
    with pytest.raises(ValueError, match="unique non-empty"):
        _named_values(["A=1", "A=2"], label="--variable")


def test_import_gitlab_cli_ready_and_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []

    class Goons:
        def import_gitlab(self, source, **kwargs):
            calls.append({"source": source, **kwargs})
            return _migration(ready=True)

    runtime = SimpleNamespace(goon=Goons())
    args = argparse.Namespace(
        source=".gitlab-ci.yml",
        output="goon.yml",
        name="converted",
        gitlab_variables=["HOST=gitlab.internal"],
        token_env="GITLAB_TOKEN",
        expected_revision="a" * 64,
    )
    monkeypatch.setenv("GITLAB_TOKEN", "secret")
    assert import_gitlab(runtime, args) == 0  # type: ignore[arg-type]
    assert calls[0]["import_variables"] == {"HOST": "gitlab.internal"}
    assert calls[0]["token"] == "secret"
    assert calls[0]["expected_revision"] == "a" * 64
    assert "Result: READY" in capsys.readouterr().out

    class BlockedGoons:
        def import_gitlab(self, source, **kwargs):
            return _migration(ready=False)

    runtime = SimpleNamespace(goon=BlockedGoons())
    args.token_env = None
    assert import_gitlab(runtime, args) == 2  # type: ignore[arg-type]
    output = capsys.readouterr().out
    assert "BLOCKING ISSUES: 1" in output
    assert "Result: BLOCKED" in output


def test_import_gitlab_cli_missing_token_env_is_error(capsys: pytest.CaptureFixture[str]) -> None:
    runtime = SimpleNamespace(goon=SimpleNamespace())
    args = argparse.Namespace(
        source=".gitlab-ci.yml",
        output="goon.yml",
        name=None,
        gitlab_variables=None,
        token_env="MISSING_GITLAB_TOKEN_FOR_TEST",
        expected_revision=None,
    )
    assert import_gitlab(runtime, args) == 2  # type: ignore[arg-type]
    assert "environment variable is not set" in capsys.readouterr().out


def test_component_fetcher_uses_first_or_fallback_template_and_token() -> None:
    requests: list[tuple[str, dict[str, str], dict[str, str]]] = []

    class Transport:
        def get(self, url, *, params, headers, timeout):
            del timeout
            requests.append((url, params, headers))
            if "templates%2Fbuild.yml" in url:
                return SimpleNamespace(status_code=404, text="missing")
            return SimpleNamespace(status_code=200, text="build:\n  script: echo ok\n")

    fetched = GitLabComponentFetcher(transport=Transport()).fetch(
        "gitlab.internal/team/catalog/build@1.2.3", token="token-value"
    )
    assert fetched.metadata.source_path == "templates/build/template.yml"
    assert fetched.metadata.project == "team/catalog"
    assert fetched.metadata.revision == "1.2.3"
    assert fetched.content.startswith("build:")
    assert requests[0][1] == {"ref": "1.2.3"}
    assert requests[0][2] == {"PRIVATE-TOKEN": "token-value"}
    assert len(requests) == 2


def test_component_fetcher_reports_http_and_transport_failures() -> None:
    responses = iter(
        [GitLabComponentTransportError("offline"), SimpleNamespace(status_code=500, text="boom")]
    )

    class Transport:
        def get(self, *args, **kwargs):
            del args, kwargs
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

    with pytest.raises(FleetPlanInvalid, match="Cannot fetch GitLab component") as exc:
        GitLabComponentFetcher(transport=Transport()).fetch(
            "gitlab.internal/team/catalog/build@1.2.3"
        )
    text = str(exc.value)
    assert "offline" in text
    assert "HTTP 500" in text


def test_component_reference_validation_and_valid_forms() -> None:
    assert parse_component_reference("gitlab.internal/team/catalog/build@1.2.3") == (
        "gitlab.internal",
        "team/catalog",
        "build",
        "1.2.3",
    )
    for value in (
        "gitlab.internal/team/catalog/build",
        "gitlab.internal/build@1.2.3",
        "gitlab.internal/team/build@",
        "gitlab.internal/team/build@~latest",
        "gitlab.internal/team/build@12",
    ):
        with pytest.raises(FleetPlanInvalid):
            parse_component_reference(value)
