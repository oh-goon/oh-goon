from __future__ import annotations

from functools import cache
import json
from pathlib import Path

import pytest

from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import (
    required_execution_tool_names,
    step_execution_tool,
)
from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, ResolvedOciBuildStep
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.fleet_plan_admission import (
    AcceptedCicdRepositoryAdmission,
    inspect_cicd_repository,
)
from oh_goon.integrations.fleet_plan_containerfile_policy import (
    oci_build_repository_policy_id,
    validate_oci_build_repository_policy,
)
from oh_goon.integrations.fleet_plan_oci_execution import execute_oci_build
from oh_goon.integrations.fleet_plan_repository_scope import bind_repository_scope
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES
from tests.factories.cicd_repository import seed_cicd_repository



def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GOON_TEMPLATES)


@cache
def _plan() -> tuple[ResolvedOciBuildStep, object]:
    plan = _resolver().resolve(
        PLAN_TEMPLATES.source("oci/image").path,
        inputs={"base_registry_hostname": "registry.example.com"},
    )
    step = plan.steps[0]
    assert isinstance(step, ResolvedOciBuildStep)
    return step, plan


def _repository(tmp_path: Path, dockerfile: str) -> Path:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    return repository


def test_oci_template_resolves_to_explicit_v25_authority() -> None:
    step, plan = _plan()

    assert plan.contract_version == 43
    assert step_execution_tool(step) == "buildah"
    assert step.capabilities.egress.mode == "hostname_allowlist"
    assert step.capabilities.egress.rules[0].hostname == "registry.example.com"
    assert step.capabilities.egress.rules[0].ports == (443,)
    assert step.policy.run_network == "none"
    assert step.policy.registry_retries == 0
    assert step.policy.pull == "always"
    assert step.policy.tls_verify is True
    assert {item.path for item in step.artifacts} == {
        "oci/image.oci.tar",
        "oci/image-build.json",
    }
    assert required_execution_tool_names(plan) == ("buildah",)
    assert plan.requirements == ()
    assert oci_build_repository_policy_id(step).startswith("oci-build-repository-policy-")


def test_oci_summary_explains_registry_and_inner_network_boundary() -> None:
    _, plan = _plan()
    summary = "\n".join(resolved_plan_summary(plan))

    assert "require containerfile_policy" not in summary
    assert "repository_policy=oci-build-repository-policy-" in summary
    assert "required_tool=buildah" in summary
    assert "registries=registry.example.com" in summary
    assert "run_network=none" in summary
    assert "registry_retries=0" in summary
    assert "archive=oci/image.oci.tar" in summary


def test_oci_containerfile_containment_uses_declared_build_context(tmp_path: Path) -> None:
    step, _ = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:" + "a" * 64 + "\n",
    )
    (repository / "build").mkdir()
    nested_context = step.model_copy(update={"context": "build"})

    assert validate_oci_build_repository_policy(repository, nested_context) == (
        "Fleet OCI Containerfile must be inside the declared build context",
    )


def test_digest_pinned_declared_base_is_admitted(tmp_path: Path) -> None:
    _, plan = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:" + "a" * 64 + "\nRUN echo ok\n",
    )

    result = inspect_cicd_repository(repository, plan.requirements)

    assert isinstance(result, AcceptedCicdRepositoryAdmission)


@pytest.mark.parametrize(
    ("dockerfile", "message"),
    [
        ("FROM registry.example.com/team/python:latest\n", "digest-pinned"),
        (
            "FROM other.example.com/team/python@sha256:" + "a" * 64 + "\n",
            "not declared",
        ),
        (
            "FROM registry.example.com/team/python@sha256:"
            + "a" * 64
            + "\nADD https://example.com/file /tmp/file\n",
            "remote ADD sources are forbidden",
        ),
        (
            "FROM registry.example.com/team/python@sha256:"
            + "a" * 64
            + "\nRUN --mount=type=secret,id=x cat /run/secrets/x\n",
            "RUN --mount is not admitted",
        ),
        (
            "FROM registry.example.com/team/python@sha256:"
            + "a" * 64
            + "\nCOPY --from=other.example.com/x/y /a /b\n",
            "earlier local build stage",
        ),
        (
            "ARG BASE=registry.example.com/team/python\nFROM ${BASE}@sha256:" + "a" * 64 + "\n",
            "variable expansion",
        ),
    ],
)
def test_unsafe_containerfile_sources_fail_typed_oci_preflight(
    tmp_path: Path, dockerfile: str, message: str
) -> None:
    _, plan = _plan()
    repository = _repository(tmp_path, dockerfile)

    with pytest.raises(FleetPlanInvalid, match=message):
        bind_repository_scope(repository, plan)


def test_multistage_copy_from_local_stage_is_admitted(tmp_path: Path) -> None:
    _, plan = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:"
        + "a" * 64
        + " AS builder\nRUN echo ok > /tmp/a\nFROM scratch\nCOPY --from=builder /tmp/a /a\n",
    )

    result = inspect_cicd_repository(repository, plan.requirements)

    assert isinstance(result, AcceptedCicdRepositoryAdmission)


def test_oci_build_execution_uses_fixed_buildah_contract_and_writes_metadata(
    tmp_path: Path,
) -> None:
    step, _ = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:" + "a" * 64 + "\n",
    )
    attempt_root = tmp_path / "attempt"
    paths = AttemptPathLayout.from_root(attempt_root)
    for value in (paths.home, paths.tmp, paths.cache, paths.config):
        value.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        seen["argv"] = argv
        seen["cwd"] = cwd
        seen["env"] = dict(env or {})
        tag = argv[argv.index("--tag") + 1]
        assert tag.startswith("oci-archive:")
        archive = Path(tag.removeprefix("oci-archive:"))
        archive.write_bytes(b"oci-archive")
        return CommandResult(
            returncode=0, stdout="built", stderr="", argv=tuple(argv), duration_ms=5
        )

    outcome = execute_oci_build(
        repository,
        step,
        paths,
        environment={"PATH": "/toolchain/bin"},
        command_runner=runner,
        cancel_requested=None,
    )

    argv = seen["argv"]
    assert isinstance(argv, tuple)
    assert argv[0] == "buildah"
    assert (argv[argv.index("--storage-driver")], argv[argv.index("--storage-driver") + 1]) == (
        "--storage-driver",
        "vfs",
    )
    assert (argv[argv.index("--network")], argv[argv.index("--network") + 1]) == (
        "--network",
        "none",
    )
    assert (argv[argv.index("--retry")], argv[argv.index("--retry") + 1]) == ("--retry", "0")
    assert "--http-proxy=false" in argv
    assert "--pull=always" in argv
    assert "--tls-verify=true" in argv
    assert outcome.returncode == 0
    metadata = json.loads((repository / "oci/image-build.json").read_text(encoding="utf-8"))
    assert metadata["schema"] == "oh-goon/oci-build-evidence/v1"
    assert metadata["run_network"] == "none"
    assert metadata["registry_retries"] == 0
    assert metadata["base_registries"] == ["registry.example.com"]
    assert len(metadata["archive_sha256"]) == 64
    assert seen["env"]["BUILD_REGISTRY_SOURCES"] == '{"allowedRegistries":["registry.example.com"]}'


def test_failed_oci_build_clears_outputs_from_the_previous_attempt(tmp_path: Path) -> None:
    step, _ = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:" + "a" * 64 + "\n",
    )
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for value in (paths.home, paths.tmp, paths.cache, paths.config):
        value.mkdir(parents=True, exist_ok=True)

    def successful_runner(*argv: str, **_kwargs):
        archive = Path(argv[argv.index("--tag") + 1].removeprefix("oci-archive:"))
        archive.write_bytes(b"first-attempt")
        return CommandResult(
            returncode=0, stdout="built", stderr="", argv=tuple(argv), duration_ms=1
        )

    first = execute_oci_build(
        repository,
        step,
        paths,
        environment={},
        command_runner=successful_runner,
        cancel_requested=None,
    )
    assert first.returncode == 0
    assert (repository / "oci/image.oci.tar").exists()
    assert (repository / "oci/image-build.json").exists()

    failed = execute_oci_build(
        repository,
        step,
        paths,
        environment={},
        command_runner=lambda *argv, **_kwargs: CommandResult(
            returncode=7, stdout="", stderr="build failed", argv=tuple(argv), duration_ms=1
        ),
        cancel_requested=None,
    )

    assert failed.returncode == 7
    assert not (repository / "oci/image.oci.tar").exists()
    assert not (repository / "oci/image-build.json").exists()


def test_failed_oci_build_durably_clears_previous_attempt_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.integrations import exact_file_write

    step, _ = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:" + "a" * 64 + "\n",
    )
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for value in (paths.home, paths.tmp, paths.cache, paths.config):
        value.mkdir(parents=True, exist_ok=True)
    output_root = repository / "oci"
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "image.oci.tar").write_bytes(b"stale-archive")
    (output_root / "image-build.json").write_text("stale-metadata", encoding="utf-8")
    synced: list[Path] = []
    monkeypatch.setattr(exact_file_write, "_fsync_directory", synced.append)

    failed = execute_oci_build(
        repository,
        step,
        paths,
        environment={},
        command_runner=lambda *argv, **_kwargs: CommandResult(
            returncode=7, stdout="", stderr="build failed", argv=tuple(argv), duration_ms=1
        ),
        cancel_requested=None,
    )

    assert failed.returncode == 7
    assert not (output_root / "image.oci.tar").exists()
    assert not (output_root / "image-build.json").exists()
    assert synced[:2] == [output_root, output_root]


def test_oci_build_metadata_failure_is_a_typed_contract_failure(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_oci_execution as oci_execution

    step, _ = _plan()
    repository = _repository(
        tmp_path,
        "FROM registry.example.com/team/python@sha256:" + "a" * 64 + "\n",
    )
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for value in (paths.home, paths.tmp, paths.cache, paths.config):
        value.mkdir(parents=True, exist_ok=True)

    def runner(*argv: str, **_kwargs):
        archive = Path(argv[argv.index("--tag") + 1].removeprefix("oci-archive:"))
        archive.write_bytes(b"current-attempt")
        return CommandResult(
            returncode=0, stdout="built", stderr="", argv=tuple(argv), duration_ms=1
        )

    monkeypatch.setattr(
        oci_execution,
        "_write_oci_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("evidence write failed")),
    )
    outcome = execute_oci_build(
        repository, step, paths, environment={}, command_runner=runner, cancel_requested=None
    )

    assert outcome.returncode == PLAN_CONTRACT_RETURN_CODE
    assert "local build evidence could not be finalized" in outcome.stderr
    assert (repository / "oci/image.oci.tar").read_bytes() == b"current-attempt"
    assert not (repository / "oci/image-build.json").exists()


def test_oci_template_rejects_invalid_registry_before_resolved_plan_exists() -> None:
    with pytest.raises(FleetPlanInvalid, match="invalid hostname syntax"):
        _resolver().resolve(
            PLAN_TEMPLATES.source("oci/image").path,
            inputs={"base_registry_hostname": "REGISTRY WITHOUT DNS"},
        )
