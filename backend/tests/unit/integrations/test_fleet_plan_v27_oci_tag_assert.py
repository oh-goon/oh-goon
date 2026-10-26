from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import (
    required_execution_tool_names,
    step_execution_tool,
)
from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, ResolvedOciTagAssertStep
from oh_goon.core.models.fleet_plan_evidence import PlanResourceEvidence
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_host_resources import (
    HostToolBinding,
    capture_host_environment,
)
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, settle_step_execution
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.executable_materialization_authority import (
    StepExecutableMaterializationReceipt,
)
from oh_goon.integrations.fleet_plan_attempt_evidence import build_strict_attempt_evidence
from oh_goon.integrations.fleet_plan_oci_tag_execution import execute_oci_tag_assert
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GOON_TEMPLATES)


DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


@cache
def _plan():
    return _resolver().resolve(
        PLAN_TEMPLATES.source("oci/tag-gate").path,
        inputs={
            "registry_hostname": "harbor.example.com",
            "repository": "team/app",
            "tag": "production",
            "expected_digest": DIGEST,
        },
    )


def _layout(root: Path) -> AttemptPathLayout:
    paths = AttemptPathLayout.from_root(root)
    for path in (paths.home, paths.tmp, paths.cache, paths.config):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def test_tag_gate_resolves_to_v27_read_only_registry_authority() -> None:
    plan = _plan()

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    assert step.target.tag_reference == "harbor.example.com/team/app:production"
    assert step.expected_digest == DIGEST
    assert step.policy.tls_verify is True
    assert step.policy.registry_retries == 0
    assert step.environment == ()
    assert step.workspace.publish is False
    assert step.cache.publish is False
    assert step_execution_tool(step) == "skopeo"
    assert step.capabilities.egress.rules[0].hostname == "harbor.example.com"
    assert {item.name for item in step.outputs} == {
        "OCI_TAG_DIGEST",
        "OCI_TAG_RESOLVED_REFERENCE",
    }
    assert required_execution_tool_names(plan) == ("skopeo",)


def test_tag_assert_summary_is_explicit_about_no_mutation_and_redacts_credentials() -> None:
    summary = "\n".join(resolved_plan_summary(_plan()))

    assert "oci_tag_assert: inspector=skopeo" in summary
    assert "target=harbor.example.com/team/app:production" in summary
    assert f"expected_digest={DIGEST}" in summary
    assert "mutation=none" in summary
    assert "credentials=host:OCI_REGISTRY_USERNAME,host:OCI_REGISTRY_PASSWORD" in summary
    assert "credential_values=redacted" in summary


def test_tag_assert_credentials_are_preflight_resources_not_step_environment(monkeypatch) -> None:
    plan = _plan()
    monkeypatch.setenv("OCI_REGISTRY_USERNAME", "robot")
    monkeypatch.setenv("OCI_REGISTRY_PASSWORD", "secret")

    captured = capture_host_environment(plan)

    assert captured == {"OCI_REGISTRY_PASSWORD": "secret", "OCI_REGISTRY_USERNAME": "robot"}
    assert plan.steps[0].environment == ()


def test_empty_tag_assert_credential_fails_before_execution_resources_are_frozen(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OCI_REGISTRY_USERNAME", "robot")
    monkeypatch.setenv("OCI_REGISTRY_PASSWORD", "")

    with pytest.raises(FleetPlanInvalid, match="non-empty.*OCI_REGISTRY_PASSWORD"):
        capture_host_environment(_plan())


def test_tag_assert_attempt_evidence_uses_frozen_skopeo_binding(
    tmp_path: Path, monkeypatch
) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    binding = HostToolBinding(
        name="skopeo",
        source_path=tmp_path / "host-skopeo",
        snapshot_path=tmp_path / "frozen-skopeo",
        identity=SnapshotIdentity(sha256="c" * 64, byte_count=4321, entry_count=1),
    )
    paths = _layout(tmp_path / "attempt")
    attempt = SimpleNamespace(
        paths=paths,
        cache_identity=SnapshotIdentity(sha256="d" * 64, byte_count=0, entry_count=0),
        egress=step.capabilities.egress,
        host_tools=(binding,),
        sandbox_bindings=(),
        managed_runtime=None,
        host_environment={},
        executable_materialization=StepExecutableMaterializationReceipt(),
    )
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_attempt_evidence.read_attempt_resource_evidence",
        lambda _root: PlanResourceEvidence(
            cpu_millis=2000, memory_bytes=2 * 1024 * 1024 * 1024, max_processes=64
        ),
    )
    observation = GitService().observe(tmp_path)

    evidence = build_strict_attempt_evidence(
        step,
        step,
        1,
        StepOutcome(argv=("skopeo",), returncode=0),
        tmp_path,
        observation,
        observation,
        attempt=attempt,  # type: ignore[arg-type]
        cache_output_identity=None,
        retained_cache=None,
        retry=None,
    )

    assert evidence.tool is not None
    assert evidence.tool.name == "skopeo"
    assert evidence.tool.origin == "host"
    assert evidence.tool.sha256 == "c" * 64
    assert evidence.tool.byte_count == 4321


@pytest.mark.parametrize(
    "quiescent,reason,expected",
    [
        (True, "none", "SUCCESS"),
        (False, "none", "INCOMPLETE"),
        (None, "none", "INCOMPLETE"),
        (True, "cancel", "CANCELLED"),
        (True, "timeout", "TIMED_OUT"),
        (True, "authority", "AUTHORITY_LOST"),
        (True, "output_limit", "OUTPUT_LIMITED"),
    ],
)
def test_tag_assert_execution_uses_private_auth_and_writes_read_only_evidence(
    tmp_path: Path,
    quiescent: bool | None,
    reason: Any,
    expected: str,
) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    paths = _layout(tmp_path / "attempt")
    seen: dict[str, object] = {}

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        seen["argv"] = argv
        seen["env"] = dict(env or {})
        authfile = Path(argv[argv.index("--authfile") + 1])
        seen["authfile"] = authfile
        seen["auth"] = json.loads(authfile.read_text(encoding="utf-8"))
        seen["auth_mode"] = authfile.stat().st_mode & 0o777
        seen["config_root"] = authfile.parent
        registries_conf = Path((env or {})["CONTAINERS_REGISTRIES_CONF"])
        seen["registries_conf"] = registries_conf.read_text(encoding="utf-8")
        return CommandResult(
            returncode=0,
            stdout=DIGEST,
            stderr="",
            argv=tuple(argv),
            duration_ms=4,
            supervisor=cast(
                CommandSupervisorReceipt,
                CommandSupervisorReceipt(
                    final_process_group_quiescent=quiescent,
                    cleanup_reason=reason,
                    process_group_cleanup_requested=reason != "none",
                )
                if quiescent is not None
                else None,
            ),
        )

    outcome = execute_oci_tag_assert(
        repository,
        step,
        paths,
        environment={"PATH": "/toolchain/bin", "REGISTRY_AUTH_FILE": "/ambient/auth.json"},
        host_environment={
            "OCI_REGISTRY_USERNAME": "robot",
            "OCI_REGISTRY_PASSWORD": "super-secret",
        },
        command_runner=runner,
        cancel_requested=None,
    )

    if expected != "SUCCESS":
        settlement = settle_step_execution(step, outcome)
        assert settlement.outcome == expected
        assert outcome.observed_returncode == 0
        assert not (repository / step.output.metadata).exists()
        assert not (repository / step.output.digest).exists()
        assert not (repository / step.output.resolved_reference).exists()
        assert isinstance(seen["authfile"], Path) and not seen["authfile"].exists()
        return

    argv = seen["argv"]
    assert outcome.returncode == 0
    assert isinstance(argv, tuple)
    assert argv[0] == "skopeo"
    assert argv[-1] == "docker://harbor.example.com/team/app:production"
    assert "--no-tags" in argv
    assert argv[argv.index("--retry-times") + 1] == "0"
    assert "--tls-verify=true" in argv
    assert argv[argv.index("--format") + 1] == "{{.Digest}}"
    assert "copy" not in argv
    assert "delete" not in argv
    assert "super-secret" not in "\n".join(argv)
    assert seen["auth_mode"] == 0o600
    assert isinstance(seen["authfile"], Path)
    assert not seen["authfile"].exists()
    env = seen["env"]
    assert isinstance(env, dict)
    assert "REGISTRY_AUTH_FILE" not in env
    assert seen["registries_conf"] == "unqualified-search-registries = []\n"
    assert isinstance(seen["config_root"], Path)
    assert not seen["config_root"].exists()
    evidence = json.loads((repository / "oci/tag-assert.json").read_text(encoding="utf-8"))
    assert evidence["schema"] == "oh-goon/oci-tag-assert-evidence/v1"
    assert evidence["operation"] == "read_only_assert"
    assert evidence["observed_digest"] == DIGEST
    assert evidence["expected_digest"] == DIGEST
    assert evidence["matched"] is True
    assert evidence["tag_mutated"] is False
    assert evidence["credential_values_recorded"] is False
    assert (repository / "oci/tag-digest.txt").read_text().strip() == DIGEST
    assert (repository / "oci/tag-resolved-reference.txt").read_text().strip() == (
        "harbor.example.com/team/app@" + DIGEST
    )


def test_tag_mismatch_fails_with_observed_evidence_and_never_runs_a_mutating_command(
    tmp_path: Path,
) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    calls: list[tuple[str, ...]] = []

    def runner(*argv: str, **kwargs):
        calls.append(tuple(argv))
        return CommandResult(
            returncode=0, stdout=OTHER_DIGEST, stderr="", argv=tuple(argv), duration_ms=2
        )

    outcome = execute_oci_tag_assert(
        repository,
        step,
        _layout(tmp_path / "attempt"),
        environment={},
        host_environment={"OCI_REGISTRY_USERNAME": "robot", "OCI_REGISTRY_PASSWORD": "secret"},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode == PLAN_CONTRACT_RETURN_CODE
    assert "no registry mutation was attempted" in outcome.stderr
    assert len(calls) == 1
    assert "inspect" in calls[0]
    assert "copy" not in calls[0]
    evidence = json.loads((repository / "oci/tag-assert.json").read_text())
    assert evidence["matched"] is False
    assert evidence["observed_digest"] == OTHER_DIGEST
    assert evidence["tag_mutated"] is False


def test_tag_observation_evidence_failure_is_typed_and_leaves_no_partial_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_oci_tag_execution as tag_execution

    step = _plan().steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    repository = tmp_path / "repo"
    repository.mkdir()

    monkeypatch.setattr(
        tag_execution,
        "_write_assert_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("metadata unavailable")),
    )
    outcome = execute_oci_tag_assert(
        repository,
        step,
        _layout(tmp_path / "attempt"),
        environment={},
        host_environment={"OCI_REGISTRY_USERNAME": "robot", "OCI_REGISTRY_PASSWORD": "secret"},
        command_runner=lambda *argv, **_kwargs: CommandResult(
            returncode=0, stdout=DIGEST, stderr="", argv=tuple(argv), duration_ms=1
        ),
        cancel_requested=None,
    )

    assert outcome.returncode == PLAN_CONTRACT_RETURN_CODE
    assert "local evidence could not be finalized" in outcome.stderr
    assert not (repository / "oci/tag-digest.txt").exists()
    assert not (repository / "oci/tag-resolved-reference.txt").exists()
    assert not (repository / "oci/tag-assert.json").exists()


def test_noncanonical_remote_digest_fails_contract(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    repository = tmp_path / "repo"
    repository.mkdir()

    def runner(*argv: str, **kwargs):
        return CommandResult(
            returncode=0, stdout="not-a-digest", stderr="", argv=tuple(argv), duration_ms=1
        )

    outcome = execute_oci_tag_assert(
        repository,
        step,
        _layout(tmp_path / "attempt"),
        environment={},
        host_environment={"OCI_REGISTRY_USERNAME": "robot", "OCI_REGISTRY_PASSWORD": "secret"},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode == PLAN_CONTRACT_RETURN_CODE
    assert "non-canonical manifest digest" in outcome.stderr


def test_unbound_runtime_digest_is_rejected_before_registry_command(tmp_path: Path) -> None:
    step = _plan().steps[0]
    assert isinstance(step, ResolvedOciTagAssertStep)
    unbound = step.model_copy(
        update={"expected_digest": "${{ steps.image.publish.outputs.OCI_DIGEST }}"}
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    called = False

    def runner(*argv: str, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("registry command must not run")

    outcome = execute_oci_tag_assert(
        repository,
        unbound,
        _layout(tmp_path / "attempt"),
        environment={},
        host_environment={"OCI_REGISTRY_USERNAME": "robot", "OCI_REGISTRY_PASSWORD": "secret"},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode == PLAN_CONTRACT_RETURN_CODE
    assert "not bound" in outcome.stderr
    assert called is False


def test_bad_tag_is_rejected_during_plan_resolution() -> None:
    with pytest.raises(FleetPlanInvalid, match="tag"):
        _resolver().resolve(
            PLAN_TEMPLATES.source("oci/tag-gate").path,
            inputs={
                "registry_hostname": "harbor.example.com",
                "repository": "team/app",
                "tag": "Production:latest",
                "expected_digest": DIGEST,
            },
        )
