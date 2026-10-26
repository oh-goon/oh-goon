from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import oh_goon.integrations.fleet_plan_oci_registry_execution as registry_execution
from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.core.errors import ExternalEffectOutcomeUnknown, FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import required_execution_tool_names
from oh_goon.core.models.fleet_plan import ResolvedOciBuildStep, ResolvedOciPublishStep
from oh_goon.core.models.fleet_plan_evidence import PlanResourceEvidence
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_host_resources import (
    HostToolBinding,
    capture_host_environment,
    preflight_host_environment_feasibility,
)
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, settle_step_execution
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.executable_materialization_authority import (
    StepExecutableMaterializationReceipt,
)
from oh_goon.integrations.fleet_plan_attempt_evidence import build_strict_attempt_evidence
from oh_goon.integrations.fleet_plan_oci_publish_execution import execute_oci_publish
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GOON_TEMPLATES)


@cache
def _plan():
    return _resolver().resolve(
        PLAN_TEMPLATES.source("oci/publish-image").path,
        inputs={
            "base_registry_hostname": "registry.example.com",
            "publish_registry_hostname": "harbor.example.com",
            "publish_repository": "team/app",
        },
    )


def _layout(root: Path) -> AttemptPathLayout:
    paths = AttemptPathLayout.from_root(root)
    for path in (paths.home, paths.tmp, paths.cache, paths.config):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _write_oci_archive(path: Path) -> str:
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "0" * 64,
                "size": 0,
            },
            "layers": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    hex_digest = hashlib.sha256(manifest).hexdigest()
    digest = f"sha256:{hex_digest}"
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": digest,
                    "size": len(manifest),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    layout = b'{"imageLayoutVersion":"1.0.0"}'
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as tar:
        for name, payload in (
            ("oci-layout", layout),
            ("index.json", index),
            (f"blobs/sha256/{hex_digest}", manifest),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(payload))
    return digest


def test_oci_credentials_fail_cheap_preflight_before_secret_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    with pytest.raises(FleetPlanInvalid, match="missing imported host environment"):
        preflight_host_environment_feasibility(
            (plan,),
            {"OCI_REGISTRY_USERNAME": "robot"},
        )

    with pytest.raises(FleetPlanInvalid, match="non-empty host credential"):
        preflight_host_environment_feasibility(
            (plan,),
            {
                "OCI_REGISTRY_USERNAME": "",
                "OCI_REGISTRY_PASSWORD": "secret",
            },
        )


def test_publish_template_resolves_to_v26_content_addressed_authority() -> None:
    plan = _plan()

    assert [step.kind for step in plan.steps] == ["oci_build", "oci_publish"]
    build, publish = plan.steps
    assert isinstance(build, ResolvedOciBuildStep)
    assert isinstance(publish, ResolvedOciPublishStep)
    assert build.workspace.publish is True
    assert publish.workspace.source.kind == "step"
    assert publish.workspace.source.step_id == build.step_id
    assert publish.workspace.publish is False
    assert publish.target.repository_reference == "harbor.example.com/team/app"
    assert publish.policy.reference_mode == "digest"
    assert publish.policy.preserve_digests is True
    assert publish.policy.registry_retries == 0
    assert publish.policy.tls_verify is True
    assert publish.retry.max_attempts == 1
    assert publish.retry.known_transient_exit_codes == ()
    assert publish.environment == ()
    assert publish.credentials.host_environment_names == (
        "OCI_REGISTRY_USERNAME",
        "OCI_REGISTRY_PASSWORD",
    )
    assert {item.name for item in publish.outputs} == {"OCI_DIGEST", "OCI_REFERENCE"}
    assert required_execution_tool_names(plan) == ("buildah", "crun", "skopeo")
    assert publish.capabilities.egress.rules[0].hostname == "harbor.example.com"


def test_publish_attempt_evidence_uses_frozen_skopeo_binding(tmp_path: Path, monkeypatch) -> None:
    publish = _plan().steps[1]
    assert isinstance(publish, ResolvedOciPublishStep)
    binding = HostToolBinding(
        name="skopeo",
        source_path=tmp_path / "host-skopeo",
        snapshot_path=tmp_path / "frozen-skopeo",
        identity=SnapshotIdentity(sha256="a" * 64, byte_count=1234, entry_count=1),
    )
    paths = _layout(tmp_path / "attempt")
    attempt = SimpleNamespace(
        paths=paths,
        cache_identity=SnapshotIdentity(sha256="d" * 64, byte_count=0, entry_count=0),
        egress=publish.capabilities.egress,
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
        publish,
        publish,
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
    assert evidence.tool.sha256 == "a" * 64
    assert evidence.tool.byte_count == 1234


def test_publish_summary_shows_authority_but_never_secret_values() -> None:
    summary = "\n".join(resolved_plan_summary(_plan()))

    assert "oci_publish: publisher=skopeo" in summary
    assert "repository=harbor.example.com/team/app" in summary
    assert "reference_mode=digest" in summary
    assert "credentials=host:OCI_REGISTRY_USERNAME,host:OCI_REGISTRY_PASSWORD" in summary
    assert "credential_values=redacted" in summary


def test_publish_credentials_are_captured_as_host_resources_without_entering_step_environment(
    monkeypatch,
) -> None:
    plan = _plan()
    monkeypatch.setenv("OCI_REGISTRY_USERNAME", "robot-user")
    monkeypatch.setenv("OCI_REGISTRY_PASSWORD", "top-secret")

    captured = capture_host_environment(plan)

    assert captured == {
        "OCI_REGISTRY_PASSWORD": "top-secret",
        "OCI_REGISTRY_USERNAME": "robot-user",
    }
    publish = plan.steps[1]
    assert publish.environment == ()


def test_missing_publish_credential_fails_before_execution_resources_are_frozen(
    monkeypatch,
) -> None:
    plan = _plan()
    monkeypatch.delenv("OCI_REGISTRY_USERNAME", raising=False)
    monkeypatch.setenv("OCI_REGISTRY_PASSWORD", "secret")

    with pytest.raises(FleetPlanInvalid, match="OCI_REGISTRY_USERNAME"):
        capture_host_environment(plan)


def test_empty_publish_credential_fails_before_execution_resources_are_frozen(monkeypatch) -> None:
    plan = _plan()
    monkeypatch.setenv("OCI_REGISTRY_USERNAME", "robot")
    monkeypatch.setenv("OCI_REGISTRY_PASSWORD", "")

    with pytest.raises(FleetPlanInvalid, match="non-empty.*OCI_REGISTRY_PASSWORD"):
        capture_host_environment(plan)


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
def test_publish_execution_uses_digest_destination_private_auth_and_nonsecret_evidence(
    tmp_path: Path,
    quiescent: bool | None,
    reason: Any,
    expected: str,
) -> None:
    plan = _plan()
    step = plan.steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    digest = _write_oci_archive(repository / step.archive)
    paths = _layout(tmp_path / "attempt")
    secret = "super-secret-password"
    seen: dict[str, object] = {}

    def runner(*argv: str, timeout=None, cwd=None, env=None, cancel_requested=None):
        seen["argv"] = argv
        seen["env"] = dict(env or {})
        authfile = Path(argv[argv.index("--dest-authfile") + 1])
        seen["authfile"] = authfile
        seen["auth_mode"] = authfile.stat().st_mode & 0o777
        seen["auth"] = json.loads(authfile.read_text(encoding="utf-8"))
        seen["config_root"] = authfile.parent
        registries_conf = Path((env or {})["CONTAINERS_REGISTRIES_CONF"])
        seen["registries_conf"] = registries_conf.read_text(encoding="utf-8")
        registries_dir = Path(argv[argv.index("--registries.d") + 1])
        seen["registries_dir_empty"] = registries_dir.is_dir() and not any(registries_dir.iterdir())
        digestfile = Path(argv[argv.index("--digestfile") + 1])
        digestfile.write_text(digest + "\n", encoding="utf-8")
        return CommandResult(
            returncode=0,
            stdout="copied",
            stderr="",
            argv=tuple(argv),
            duration_ms=9,
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

    outcome = execute_oci_publish(
        repository,
        step,
        paths,
        environment={"PATH": "/toolchain/bin", "HOME": str(paths.home)},
        host_environment={"OCI_REGISTRY_USERNAME": "robot", "OCI_REGISTRY_PASSWORD": secret},
        command_runner=runner,
        cancel_requested=None,
    )

    if expected != "SUCCESS":
        settlement = settle_step_execution(step, outcome)
        assert settlement.outcome == expected
        assert outcome.observed_returncode == 0 and outcome.release_effect is None
        assert not (repository / step.output.metadata).exists()
        assert not (repository / step.output.reference).exists()
        assert isinstance(seen["authfile"], Path) and not seen["authfile"].exists()
        return

    argv = seen["argv"]
    assert outcome.returncode == 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "OCI_PUBLISH"
    assert outcome.release_effect.manifest_digest == digest
    assert outcome.release_effect.published_reference == f"harbor.example.com/team/app@{digest}"
    assert outcome.release_effect.digest_preserved is True
    assert isinstance(argv, tuple)
    assert argv[0] == "skopeo"
    assert "--preserve-digests" in argv
    assert (argv[argv.index("--retry-times") + 1]) == "0"
    assert (argv[argv.index("--image-parallel-copies") + 1]) == "1"
    assert (argv[argv.index("--tmpdir") + 1]) == str(paths.tmp)
    assert seen["registries_dir_empty"] is True
    assert "--dest-tls-verify=true" in argv
    assert argv[-1] == f"docker://harbor.example.com/team/app@{digest}"
    assert secret not in "\n".join(argv)
    assert secret not in json.dumps(seen["env"])
    assert "REGISTRY_AUTH_FILE" not in seen["env"]
    assert seen["registries_conf"] == "unqualified-search-registries = []\n"
    assert isinstance(seen["config_root"], Path)
    assert not seen["config_root"].exists()
    assert "CONTAINERS_REGISTRIES_CONF_OVERRIDE" not in seen["env"]
    assert "REGISTRIES_CONFIG_PATH" not in seen["env"]
    assert seen["auth_mode"] == 0o600
    assert isinstance(seen["authfile"], Path)
    assert not seen["authfile"].exists()
    assert secret not in json.dumps(json.loads((repository / "oci/image-publish.json").read_text()))
    assert (repository / "oci/image-digest.txt").read_text().strip() == digest
    assert (
        repository / "oci/image-reference.txt"
    ).read_text().strip() == f"harbor.example.com/team/app@{digest}"
    evidence = json.loads((repository / "oci/image-publish.json").read_text())
    assert evidence["schema"] == "oh-goon/oci-publish-evidence/v1"
    assert evidence["source_manifest_digest"] == digest
    assert evidence["parallel_copies"] == 1
    assert evidence["credential_values_recorded"] is False


def test_committed_publish_preserves_release_fact_when_local_metadata_write_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_oci_publish_execution as publish_execution

    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    digest = _write_oci_archive(repository / step.archive)
    paths = _layout(tmp_path / "attempt")

    def runner(*argv: str, **_kwargs):
        digestfile = Path(argv[argv.index("--digestfile") + 1])
        digestfile.write_text(digest + "\n", encoding="utf-8")
        return CommandResult(
            returncode=0, stdout="copied", stderr="", argv=tuple(argv), duration_ms=7
        )

    def fail_metadata(*_args, **_kwargs):
        raise OSError("simulated local evidence failure")

    monkeypatch.setattr(publish_execution, "_write_publish_evidence", fail_metadata)
    outcome = execute_oci_publish(
        repository,
        step,
        paths,
        environment={"PATH": "/toolchain/bin"},
        host_environment={
            "OCI_REGISTRY_USERNAME": "robot",
            "OCI_REGISTRY_PASSWORD": "secret-value",
        },
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode != 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "OCI_PUBLISH"
    assert outcome.release_effect.manifest_digest == digest
    assert "publication committed" in outcome.stderr
    assert not (repository / "oci/image-digest.txt").exists()
    assert not (repository / "oci/image-reference.txt").exists()
    assert not (repository / "oci/image-publish.json").exists()


def test_committed_publish_reports_private_cleanup_failure_and_preserves_release_fact(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_oci_publish_execution as publish_execution

    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    digest = _write_oci_archive(repository / step.archive)
    paths = _layout(tmp_path / "attempt")

    def runner(*argv: str, **_kwargs):
        Path(argv[argv.index("--digestfile") + 1]).write_text(digest + "\n", encoding="utf-8")
        return CommandResult(
            returncode=0, stdout="copied", stderr="", argv=tuple(argv), duration_ms=7
        )

    monkeypatch.setattr(
        publish_execution,
        "cleanup_oci_registry_config",
        lambda _config: (_ for _ in ()).throw(OSError("simulated credential cleanup failure")),
    )
    outcome = execute_oci_publish(
        repository,
        step,
        paths,
        environment={"PATH": "/toolchain/bin"},
        host_environment={
            "OCI_REGISTRY_USERNAME": "robot",
            "OCI_REGISTRY_PASSWORD": "secret-value",
        },
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode != 0
    assert outcome.release_effect is not None
    assert outcome.release_effect.kind == "OCI_PUBLISH"
    assert outcome.release_effect.manifest_digest == digest
    assert "credential cleanup failed" in outcome.stderr
    from oh_goon.integrations._fleet_plan_step_outcome import settle_step_execution

    assert settle_step_execution(step, outcome).cleanup == "UNVERIFIED"
    assert not (repository / "oci/image-digest.txt").exists()
    assert not (repository / "oci/image-reference.txt").exists()
    assert not (repository / "oci/image-publish.json").exists()


def test_publish_nonzero_after_copy_start_is_outcome_unknown(tmp_path: Path) -> None:
    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    _write_oci_archive(repository / step.archive)

    def runner(*argv: str, **_kwargs):
        return CommandResult(
            returncode=1,
            stdout="",
            stderr="registry connection reset",
            argv=tuple(argv),
            duration_ms=7,
        )

    with pytest.raises(
        ExternalEffectOutcomeUnknown, match="remote publication outcome may be unknown"
    ):
        execute_oci_publish(
            repository,
            step,
            _layout(tmp_path / "attempt"),
            environment={"PATH": "/toolchain/bin"},
            host_environment={
                "OCI_REGISTRY_USERNAME": "robot",
                "OCI_REGISTRY_PASSWORD": "secret-value",
            },
            command_runner=runner,
            cancel_requested=None,
        )


def test_publish_timeout_after_copy_start_is_outcome_unknown(tmp_path: Path) -> None:
    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    _write_oci_archive(repository / step.archive)

    def runner(*argv: str, **_kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=step.timeout_seconds)

    with pytest.raises(ExternalEffectOutcomeUnknown, match="registry outcome may be unknown"):
        execute_oci_publish(
            repository,
            step,
            _layout(tmp_path / "attempt"),
            environment={"PATH": "/toolchain/bin"},
            host_environment={
                "OCI_REGISTRY_USERNAME": "robot",
                "OCI_REGISTRY_PASSWORD": "secret-value",
            },
            command_runner=runner,
            cancel_requested=None,
        )


def test_invalid_archive_fails_before_network_command(tmp_path: Path) -> None:
    plan = _plan()
    step = plan.steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    (repository / "oci").mkdir(parents=True)
    (repository / step.archive).write_bytes(b"not an OCI archive")
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network command must not run")

    outcome = execute_oci_publish(
        repository,
        step,
        _layout(tmp_path / "attempt"),
        environment={},
        host_environment={"OCI_REGISTRY_USERNAME": "robot", "OCI_REGISTRY_PASSWORD": "secret"},
        command_runner=runner,
        cancel_requested=None,
    )

    assert outcome.returncode == 125
    assert called is False


def test_publish_repository_rejects_mutable_tag_syntax_before_plan_exists() -> None:
    with pytest.raises(FleetPlanInvalid, match="invalid oci_repository syntax"):
        _resolver().resolve(
            PLAN_TEMPLATES.source("oci/publish-image").path,
            inputs={
                "base_registry_hostname": "registry.example.com",
                "publish_registry_hostname": "harbor.example.com",
                "publish_repository": "team/app:latest",
            },
        )


def test_registry_private_config_failure_removes_already_written_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    paths = _layout(tmp_path / "attempt")
    original = registry_execution.publish_text_file

    def fail_after_auth(target: Path, content: str, **kwargs) -> None:
        if target.name == "registries.conf":
            raise OSError("simulated registry config failure")
        original(target, content, **kwargs)

    monkeypatch.setattr(registry_execution, "publish_text_file", fail_after_auth)
    with pytest.raises(OSError, match="simulated registry config failure"):
        registry_execution.prepare_oci_registry_config(
            paths,
            namespace="failure-clean",
            image_authority=step.target.registry.image_authority,
            username_host_env=step.credentials.username_host_env,
            password_host_env=step.credentials.password_host_env,
            host_environment={
                "OCI_REGISTRY_USERNAME": "robot",
                "OCI_REGISTRY_PASSWORD": "secret-value",
            },
        )

    assert not (paths.config / "failure-clean").exists()


def test_registry_preparation_surfaces_private_cleanup_failure(tmp_path: Path, monkeypatch) -> None:
    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    paths = _layout(tmp_path / "attempt")
    original = registry_execution.publish_text_file

    def fail_after_auth(target: Path, content: str, **kwargs) -> None:
        if target.name == "registries.conf":
            raise OSError("simulated registry config failure")
        original(target, content, **kwargs)

    monkeypatch.setattr(registry_execution, "publish_text_file", fail_after_auth)
    monkeypatch.setattr(
        registry_execution,
        "remove_attempt_private_root",
        lambda _root: (_ for _ in ()).throw(OSError("simulated cleanup failure")),
    )
    with pytest.raises(OSError, match="private config cleanup failed.*simulated cleanup failure"):
        registry_execution.prepare_oci_registry_config(
            paths,
            namespace="cleanup-failure",
            image_authority=step.target.registry.image_authority,
            username_host_env=step.credentials.username_host_env,
            password_host_env=step.credentials.password_host_env,
            host_environment={
                "OCI_REGISTRY_USERNAME": "robot",
                "OCI_REGISTRY_PASSWORD": "secret-value",
            },
        )


def test_publish_policy_failure_removes_completed_private_registry_config(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.integrations.fleet_plan_oci_publish_execution as publish_execution

    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    repository = tmp_path / "repo"
    repository.mkdir()
    _write_oci_archive(repository / step.archive)
    paths = _layout(tmp_path / "attempt")

    def fail_policy(_config):
        raise OSError("simulated policy failure")

    monkeypatch.setattr(publish_execution, "_prepare_publish_policy", fail_policy)
    outcome = execute_oci_publish(
        repository,
        step,
        paths,
        environment={"PATH": "/toolchain/bin"},
        host_environment={
            "OCI_REGISTRY_USERNAME": "robot",
            "OCI_REGISTRY_PASSWORD": "secret-value",
        },
        command_runner=lambda *_args, **_kwargs: pytest.fail("network command must not run"),
        cancel_requested=None,
    )

    assert outcome.returncode != 0
    assert "simulated policy failure" in outcome.stderr
    assert not (paths.config / "oci-publish").exists()


def test_registry_private_config_rejects_symlink_root_without_writing_credentials(
    tmp_path: Path,
) -> None:
    step = _plan().steps[1]
    assert isinstance(step, ResolvedOciPublishStep)
    paths = _layout(tmp_path / "attempt")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    root = paths.config / "symlink-root"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="must be an owned directory"):
        registry_execution.prepare_oci_registry_config(
            paths,
            namespace="symlink-root",
            image_authority=step.target.registry.image_authority,
            username_host_env=step.credentials.username_host_env,
            password_host_env=step.credentials.password_host_env,
            host_environment={
                "OCI_REGISTRY_USERNAME": "robot",
                "OCI_REGISTRY_PASSWORD": "secret-value",
            },
        )

    assert root.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (outside / "auth.json").exists()


def test_private_root_cleanup_removes_broken_symlink_entry(tmp_path: Path) -> None:
    from oh_goon.integrations.fleet_plan_private_root import remove_attempt_private_root

    root = tmp_path / "broken-private-root"
    root.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    assert root.is_symlink()
    assert not root.exists()

    remove_attempt_private_root(root)

    assert not root.is_symlink()
    assert not root.exists()
