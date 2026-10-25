from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from oh_goon.core.errors import ExternalEffectOutcomeUnknown
from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, ResolvedOciPublishStep
from oh_goon.core.models.fleet_plan_release_evidence import OciPublishReleaseEvidence
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    from_command,
    process_boundary_settled,
)
from oh_goon.integrations.exact_file_write import publish_text_file, remove_file_durably
from oh_goon.integrations.fleet_plan_oci_registry_execution import (
    OciRegistryAttemptConfig,
    cleanup_oci_registry_config,
    oci_registry_environment,
    prepare_oci_registry_config,
)


class OciPublishCommandRunner(Protocol):
    def __call__(
        self,
        *args: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class _PublishPaths:
    archive: Path
    metadata: Path
    digest: Path
    reference: Path


def execute_oci_publish(
    repository: Path,
    step: ResolvedOciPublishStep,
    paths: AttemptPathLayout,
    *,
    environment: Mapping[str, str],
    host_environment: Mapping[str, str],
    command_runner: OciPublishCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    """Publish one already-built archive to its content-addressed registry reference."""

    outputs = _resolve_publish_paths(repository, step)
    _clear_old_outputs(outputs)
    try:
        manifest_digest = _validated_archive_manifest_digest(outputs.archive)
    except (OSError, ValueError, tarfile.TarError) as exc:
        return _contract_failure(str(exc))
    prepared = _prepare_registry_publish(step, paths, host_environment)
    if isinstance(prepared, StepOutcome):
        return prepared
    registry_config, policy = prepared
    destination = step.target.digest_reference(manifest_digest)
    result, cleanup_error = _run_publish_command(
        repository,
        step,
        outputs,
        registry_config,
        policy,
        destination,
        environment,
        command_runner,
        cancel_requested,
    )
    outcome = from_command(result)
    if result.returncode != 0:
        _clear_old_outputs(outputs)
        message = (
            "OCI publication command did not prove an authoritative remote rejection/success; "
            f"skopeo_exit={result.returncode}. Automatic replay is blocked because the remote "
            "publication outcome may be unknown."
        )
        if cleanup_error is not None:
            message += f" Private registry credential cleanup also failed: {cleanup_error}"
        raise ExternalEffectOutcomeUnknown(message)
    if outcome.returncode != 0 or process_boundary_settled(outcome) is not True:
        if cleanup_error is not None:
            return replace(
                _contract_failure(
                    f"Private registry credential cleanup failed: {cleanup_error}", outcome
                ),
                resource_cleanup="unverified",
            )
        return outcome
    try:
        written_digest = outputs.digest.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _clear_old_outputs(outputs)
        return _contract_failure(
            f"Fleet OCI publisher did not produce digest evidence: {exc}", outcome
        )
    if written_digest != manifest_digest:
        _clear_old_outputs(outputs)
        return _contract_failure(
            "Fleet OCI publisher returned a digest different from the admitted source manifest",
            outcome,
        )
    released = replace(
        outcome,
        release_effect=OciPublishReleaseEvidence(
            registry_authority=step.target.registry.image_authority,
            repository=step.target.repository,
            manifest_digest=manifest_digest,
            published_reference=destination,
        ),
    )
    if cleanup_error is not None:
        _clear_old_outputs(outputs)
        return replace(
            _contract_failure(
                f"OCI publication committed but private registry credential cleanup failed: {cleanup_error}",
                released,
            ),
            resource_cleanup="unverified",
        )
    return _finalize_publish_outputs(step, outputs, destination, manifest_digest, released)


def _prepare_registry_publish(
    step: ResolvedOciPublishStep,
    paths: AttemptPathLayout,
    host_environment: Mapping[str, str],
) -> tuple[OciRegistryAttemptConfig, Path] | StepOutcome:
    registry_config: OciRegistryAttemptConfig | None = None
    try:
        registry_config = prepare_oci_registry_config(
            paths,
            namespace="oci-publish",
            image_authority=step.target.registry.image_authority,
            username_host_env=step.credentials.username_host_env,
            password_host_env=step.credentials.password_host_env,
            host_environment=host_environment,
        )
        return registry_config, _prepare_publish_policy(registry_config)
    except (OSError, ValueError, KeyError) as exc:
        if registry_config is not None:
            cleanup_error = _cleanup_error(registry_config)
            if cleanup_error is not None:
                return _contract_failure(
                    f"{exc}; private registry credential cleanup failed: {cleanup_error}"
                )
        return _contract_failure(str(exc))


def _run_publish_command(
    repository: Path,
    step: ResolvedOciPublishStep,
    outputs: _PublishPaths,
    config: OciRegistryAttemptConfig,
    policy: Path,
    destination: str,
    environment: Mapping[str, str],
    command_runner: OciPublishCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> tuple[CommandResult, OSError | None]:
    try:
        result = command_runner(
            *_skopeo_copy_argv(step, outputs, config, policy, destination),
            cwd=repository,
            timeout=step.timeout_seconds,
            cancel_requested=cancel_requested,
            env=oci_registry_environment(environment, config),
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup_with_note(config, exc)
        raise ExternalEffectOutcomeUnknown(
            "OCI publication timed out after the remote copy could have started. Automatic replay "
            "is blocked because the registry outcome may be unknown."
        ) from exc
    except BaseException as exc:
        _cleanup_with_note(config, exc)
        raise
    return result, _cleanup_error(config)


def _cleanup_error(config: OciRegistryAttemptConfig) -> OSError | None:
    try:
        cleanup_oci_registry_config(config)
    except OSError as exc:
        return exc
    return None


def _cleanup_with_note(config: OciRegistryAttemptConfig, error: BaseException) -> None:
    cleanup_error = _cleanup_error(config)
    if cleanup_error is not None:
        error.add_note(f"Private registry credential cleanup also failed: {cleanup_error}")


def _cleanup_contract_failure(outcome: StepOutcome, cleanup_error: OSError | None) -> StepOutcome:
    if cleanup_error is None:
        return outcome
    return replace(
        _contract_failure(f"Private registry credential cleanup failed: {cleanup_error}", outcome),
        resource_cleanup="unverified",
    )


def _finalize_publish_outputs(
    step: ResolvedOciPublishStep,
    outputs: _PublishPaths,
    destination: str,
    manifest_digest: str,
    released: StepOutcome,
) -> StepOutcome:
    try:
        publish_text_file(outputs.digest, manifest_digest + "\n")
        publish_text_file(outputs.reference, destination + "\n")
        _write_publish_evidence(step, outputs, destination, manifest_digest)
    except OSError as exc:
        _clear_old_outputs(outputs)
        return _contract_failure(
            "OCI publication committed but local release evidence could not be finalized: "
            f"{exc.__class__.__name__}",
            released,
        )
    return released


def _resolve_publish_paths(repository: Path, step: ResolvedOciPublishStep) -> _PublishPaths:
    space = RepositoryPathSpace.from_root(repository)
    archive = space.resolve(step.archive, label="Fleet OCI publish archive", require_exists=True)
    metadata = space.resolve(step.output.metadata, label="Fleet OCI publish metadata")
    digest = space.resolve(step.output.digest, label="Fleet OCI publish digest")
    reference = space.resolve(step.output.reference, label="Fleet OCI publish reference")
    for path in (metadata, digest, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
    return _PublishPaths(archive=archive, metadata=metadata, digest=digest, reference=reference)


def _clear_old_outputs(paths: _PublishPaths) -> None:
    for path in (paths.metadata, paths.digest, paths.reference):
        remove_file_durably(path)


def _validated_archive_manifest_digest(archive: Path) -> str:
    with tarfile.open(archive, mode="r:*") as tar:
        names = {member.name for member in tar.getmembers() if member.isfile()}
        if any(_unsafe_tar_name(name) for name in names):
            raise ValueError("Fleet OCI archive contains an unsafe path")
        layout = _json_tar_member(tar, "oci-layout", max_bytes=4096)
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError("Fleet OCI archive has unsupported oci-layout metadata")
        index = _json_tar_member(tar, "index.json", max_bytes=1024 * 1024)
        manifests = index.get("manifests") if isinstance(index, dict) else None
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError(
                "Fleet OCI publish requires an archive containing exactly one manifest descriptor"
            )
        descriptor = manifests[0]
        if not isinstance(descriptor, dict):
            raise ValueError("Fleet OCI archive manifest descriptor is invalid")
        digest = descriptor.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
            raise ValueError("Fleet OCI archive manifest digest must be sha256:<64 lowercase hex>")
        hex_digest = digest.removeprefix("sha256:")
        if any(ch not in "0123456789abcdef" for ch in hex_digest):
            raise ValueError("Fleet OCI archive manifest digest must be sha256:<64 lowercase hex>")
        blob_name = f"blobs/sha256/{hex_digest}"
        member = tar.getmember(blob_name) if blob_name in names else None
        if member is None:
            raise ValueError("Fleet OCI archive is missing its declared manifest blob")
        stream = tar.extractfile(member)
        if stream is None:
            raise ValueError("Fleet OCI archive manifest blob cannot be read")
        blob = stream.read(8 * 1024 * 1024 + 1)
        if len(blob) > 8 * 1024 * 1024:
            raise ValueError("Fleet OCI archive manifest blob exceeds 8 MiB")
        if hashlib.sha256(blob).hexdigest() != hex_digest:
            raise ValueError("Fleet OCI archive manifest blob does not match its declared digest")
        size = descriptor.get("size")
        if type(size) is not int or size != len(blob):
            raise ValueError("Fleet OCI archive manifest descriptor size does not match the blob")
        return digest


def _unsafe_tar_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or "\\" in name


def _json_tar_member(tar: tarfile.TarFile, name: str, *, max_bytes: int) -> object:
    try:
        member = tar.getmember(name)
    except KeyError as exc:
        raise ValueError(f"Fleet OCI archive is missing {name}") from exc
    if not member.isfile() or member.size > max_bytes:
        raise ValueError(f"Fleet OCI archive {name} is not a bounded regular file")
    stream = tar.extractfile(member)
    if stream is None:
        raise ValueError(f"Fleet OCI archive {name} cannot be read")
    try:
        data: object = json.loads(stream.read(max_bytes + 1))
        return data
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fleet OCI archive {name} is not valid JSON") from exc


def _prepare_publish_policy(config: OciRegistryAttemptConfig) -> Path:
    policy = config.root / "policy.json"
    publish_text_file(
        policy,
        json.dumps(
            {
                "default": [{"type": "reject"}],
                "transports": {"oci-archive": {"": [{"type": "insecureAcceptAnything"}]}},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    policy.chmod(0o600)
    return policy


def _skopeo_copy_argv(
    step: ResolvedOciPublishStep,
    outputs: _PublishPaths,
    config: OciRegistryAttemptConfig,
    policy: Path,
    destination: str,
) -> tuple[str, ...]:
    return (
        "skopeo",
        "--policy",
        str(policy),
        "--registries.d",
        str(config.registries_dir),
        "--tmpdir",
        str(config.tmpdir),
        "copy",
        "--dest-authfile",
        str(config.authfile),
        f"--dest-tls-verify={'true' if step.policy.tls_verify else 'false'}",
        "--preserve-digests",
        "--retry-times",
        str(step.policy.registry_retries),
        "--image-parallel-copies",
        str(step.policy.parallel_copies),
        "--digestfile",
        str(outputs.digest),
        f"oci-archive:{outputs.archive}",
        f"docker://{destination}",
    )


def _write_publish_evidence(
    step: ResolvedOciPublishStep,
    paths: _PublishPaths,
    destination: str,
    digest: str,
) -> None:
    payload = {
        "schema": "oh-goon/oci-publish-evidence/v1",
        "archive": step.archive,
        "archive_sha256": _sha256(paths.archive),
        "source_manifest_digest": digest,
        "published_reference": destination,
        "reference_mode": step.policy.reference_mode,
        "preserve_digests": step.policy.preserve_digests,
        "registry_retries": step.policy.registry_retries,
        "parallel_copies": step.policy.parallel_copies,
        "tls_verify": step.policy.tls_verify,
        "credential_sources": [
            step.credentials.username_host_env,
            step.credentials.password_host_env,
        ],
        "credential_values_recorded": False,
    }
    publish_text_file(paths.metadata, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_failure(message: str, outcome: StepOutcome | None = None) -> StepOutcome:
    base = outcome or StepOutcome(
        argv=("skopeo",), returncode=PLAN_CONTRACT_RETURN_CODE, stdout="", stderr="", duration_ms=0
    )
    return replace(
        base,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stderr=(base.stderr + f"\n{message}").strip(),
    )
