from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, ResolvedOciTagAssertStep
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, from_command
from oh_goon.integrations.exact_file_write import publish_text_file, remove_file_durably
from oh_goon.integrations.fleet_plan_oci_registry_execution import (
    OciRegistryAttemptConfig,
    cleanup_oci_registry_config,
    oci_registry_environment,
    prepare_oci_registry_config,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class OciTagAssertCommandRunner(Protocol):
    def __call__(
        self,
        *args: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class _TagAssertPaths:
    metadata: Path
    digest: Path
    resolved_reference: Path


def execute_oci_tag_assert(
    repository: Path,
    step: ResolvedOciTagAssertStep,
    paths: AttemptPathLayout,
    *,
    environment: Mapping[str, str],
    host_environment: Mapping[str, str],
    command_runner: OciTagAssertCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    """Read one mutable tag and require that it currently resolves to the expected digest."""

    outputs = _resolve_output_paths(repository, step)
    _clear_old_outputs(outputs)
    if _DIGEST.fullmatch(step.expected_digest) is None:
        return _contract_failure(
            "Fleet OCI tag assertion expected_digest was not bound to one canonical sha256 digest before execution"
        )
    try:
        config = prepare_oci_registry_config(
            paths,
            namespace="oci-tag-assert",
            image_authority=step.target.registry.image_authority,
            username_host_env=step.credentials.username_host_env,
            password_host_env=step.credentials.password_host_env,
            host_environment=host_environment,
        )
    except (OSError, ValueError, KeyError) as exc:
        return _contract_failure(str(exc))
    result, cleanup_error = _run_tag_command(
        repository, step, config, environment, command_runner, cancel_requested
    )
    outcome = from_command(result)
    if cleanup_error is not None:
        _clear_old_outputs(outputs)
        return _contract_failure(
            f"Private registry credential cleanup failed: {cleanup_error}", outcome
        )
    if result.returncode != 0:
        return outcome
    observed_digest = result.stdout.strip()
    if _DIGEST.fullmatch(observed_digest) is None:
        return _contract_failure(
            "Fleet OCI tag assertion received a non-canonical manifest digest from Skopeo",
            outcome,
        )
    resolved_reference = step.target.digest_reference(observed_digest)
    matched = observed_digest == step.expected_digest
    try:
        publish_text_file(outputs.digest, observed_digest + "\n")
        publish_text_file(outputs.resolved_reference, resolved_reference + "\n")
        _write_assert_evidence(step, outputs, observed_digest, resolved_reference, matched)
    except OSError as exc:
        _clear_old_outputs(outputs)
        return _contract_failure(
            "Fleet OCI tag observation completed but local evidence could not be finalized: "
            f"{exc.__class__.__name__}",
            outcome,
        )
    if not matched:
        return _contract_failure(
            f"Fleet OCI tag {step.target.tag_reference} resolves to {observed_digest}, "
            f"expected {step.expected_digest}; no registry mutation was attempted",
            outcome,
        )
    return outcome


def _run_tag_command(
    repository: Path,
    step: ResolvedOciTagAssertStep,
    config: OciRegistryAttemptConfig,
    environment: Mapping[str, str],
    command_runner: OciTagAssertCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> tuple[CommandResult, OSError | None]:
    try:
        result = command_runner(
            *_skopeo_inspect_argv(step, config),
            cwd=repository,
            timeout=step.timeout_seconds,
            cancel_requested=cancel_requested,
            env=oci_registry_environment(environment, config),
        )
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


def _resolve_output_paths(repository: Path, step: ResolvedOciTagAssertStep) -> _TagAssertPaths:
    space = RepositoryPathSpace.from_root(repository)
    metadata = space.resolve(step.output.metadata, label="Fleet OCI tag assertion metadata")
    digest = space.resolve(step.output.digest, label="Fleet OCI tag assertion digest")
    resolved_reference = space.resolve(
        step.output.resolved_reference,
        label="Fleet OCI tag assertion resolved reference",
    )
    for path in (metadata, digest, resolved_reference):
        path.parent.mkdir(parents=True, exist_ok=True)
    return _TagAssertPaths(
        metadata=metadata,
        digest=digest,
        resolved_reference=resolved_reference,
    )


def _clear_old_outputs(paths: _TagAssertPaths) -> None:
    for path in (paths.metadata, paths.digest, paths.resolved_reference):
        remove_file_durably(path)


def _skopeo_inspect_argv(
    step: ResolvedOciTagAssertStep,
    config: OciRegistryAttemptConfig,
) -> tuple[str, ...]:
    return (
        "skopeo",
        "--registries.d",
        str(config.registries_dir),
        "--tmpdir",
        str(config.tmpdir),
        "inspect",
        "--authfile",
        str(config.authfile),
        f"--tls-verify={'true' if step.policy.tls_verify else 'false'}",
        "--retry-times",
        str(step.policy.registry_retries),
        "--no-tags",
        "--format",
        "{{.Digest}}",
        f"docker://{step.target.tag_reference}",
    )


def _write_assert_evidence(
    step: ResolvedOciTagAssertStep,
    paths: _TagAssertPaths,
    observed_digest: str,
    resolved_reference: str,
    matched: bool,
) -> None:
    payload = {
        "schema": "oh-goon/oci-tag-assert-evidence/v1",
        "operation": "read_only_assert",
        "target_tag_reference": step.target.tag_reference,
        "expected_digest": step.expected_digest,
        "observed_digest": observed_digest,
        "resolved_reference": resolved_reference,
        "matched": matched,
        "tag_mutated": False,
        "tls_verify": step.policy.tls_verify,
        "registry_retries": step.policy.registry_retries,
        "credential_sources": [
            step.credentials.username_host_env,
            step.credentials.password_host_env,
        ],
        "credential_values_recorded": False,
    }
    publish_text_file(paths.metadata, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _contract_failure(message: str, outcome: StepOutcome | None = None) -> StepOutcome:
    base = outcome or StepOutcome(
        argv=("skopeo",),
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stdout="",
        stderr="",
        duration_ms=0,
    )
    return StepOutcome(
        argv=base.argv,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stdout=base.stdout,
        stderr=(base.stderr + f"\n{message}").strip(),
        duration_ms=base.duration_ms,
    )
