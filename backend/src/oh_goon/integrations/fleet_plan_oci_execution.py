from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, ResolvedOciBuildStep
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, from_command
from oh_goon.integrations.exact_file_write import publish_text_file, remove_file_durably


class OciCommandRunner(Protocol):
    def __call__(
        self,
        *args: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class _OciPaths:
    archive: Path
    metadata: Path
    containerfile: Path
    context: Path


@dataclass(frozen=True)
class _BuildahConfig:
    registries_conf: Path
    shortnames_conf: Path
    authfile: Path
    hooks_dir: Path


def execute_oci_build(
    repository: Path,
    step: ResolvedOciBuildStep,
    paths: AttemptPathLayout,
    *,
    environment: Mapping[str, str],
    command_runner: OciCommandRunner,
    cancel_requested: Callable[[], bool] | None,
) -> StepOutcome:
    """Execute the fixed Buildah contract and emit inspectable local build evidence."""

    outputs = _resolve_oci_paths(repository, step)
    _clear_oci_outputs(outputs)
    config = _prepare_buildah_config(paths)
    result = command_runner(
        *_buildah_argv(step, paths, outputs, config),
        cwd=repository,
        timeout=step.timeout_seconds,
        cancel_requested=cancel_requested,
        env=_oci_environment(environment, step),
    )
    outcome = from_command(result)
    if result.returncode != 0:
        _clear_oci_outputs(outputs)
        return outcome
    if not outputs.archive.is_file():
        return _missing_archive_outcome(outcome)
    try:
        _write_oci_evidence(step, outputs)
    except OSError as exc:
        return _contract_failure(
            "Fleet OCI build completed but local build evidence could not be finalized: "
            f"{exc.__class__.__name__}",
            outcome,
        )
    return outcome


def _resolve_oci_paths(repository: Path, step: ResolvedOciBuildStep) -> _OciPaths:
    space = RepositoryPathSpace.from_root(repository)
    archive = space.resolve(step.output.archive, label="Fleet OCI archive output")
    metadata = space.resolve(step.output.metadata, label="Fleet OCI metadata output")
    containerfile = space.resolve(
        step.containerfile,
        label="Fleet OCI Containerfile",
        require_exists=True,
    )
    context = space.resolve(
        step.context,
        label="Fleet OCI build context",
        kind="directory",
        require_exists=True,
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    return _OciPaths(archive, metadata, containerfile, context)


def _clear_oci_outputs(paths: _OciPaths) -> None:
    for path in (paths.archive, paths.metadata):
        remove_file_durably(path)


def _prepare_buildah_config(paths: AttemptPathLayout) -> _BuildahConfig:
    oci_config = paths.config / "oci"
    hooks_dir = oci_config / "hooks"
    oci_config.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    registries_conf = oci_config / "registries.conf"
    shortnames_conf = oci_config / "shortnames.conf"
    authfile = oci_config / "auth.json"
    registries_conf.write_text("unqualified-search-registries = []\n", encoding="utf-8")
    shortnames_conf.write_text("", encoding="utf-8")
    authfile.write_text('{"auths":{}}\n', encoding="utf-8")
    return _BuildahConfig(registries_conf, shortnames_conf, authfile, hooks_dir)


def _buildah_argv(
    step: ResolvedOciBuildStep,
    paths: AttemptPathLayout,
    outputs: _OciPaths,
    config: _BuildahConfig,
) -> tuple[str, ...]:
    return (
        "buildah",
        "--root",
        str(paths.cache / "buildah-root"),
        "--runroot",
        str(paths.tmp / "buildah-runroot"),
        "--storage-driver",
        "vfs",
        "--registries-conf",
        str(config.registries_conf),
        "--short-name-alias-conf",
        str(config.shortnames_conf),
        "build",
        "--authfile",
        str(config.authfile),
        "--hooks-dir",
        str(config.hooks_dir),
        "--format",
        "oci",
        "--isolation",
        "chroot",
        "--network",
        step.policy.run_network,
        "--http-proxy=false",
        f"--pull={step.policy.pull}",
        "--retry",
        str(step.policy.registry_retries),
        f"--tls-verify={'true' if step.policy.tls_verify else 'false'}",
        "--timestamp",
        str(step.policy.source_date_epoch),
        "--platform",
        step.policy.platform,
        "--tag",
        f"oci-archive:{outputs.archive}",
        "--file",
        str(outputs.containerfile),
        str(outputs.context),
    )


def _oci_environment(
    environment: Mapping[str, str],
    step: ResolvedOciBuildStep,
) -> dict[str, str]:
    result = dict(environment)
    if step.registries:
        result["BUILD_REGISTRY_SOURCES"] = json.dumps(
            {"allowedRegistries": [item.image_authority for item in step.registries]},
            sort_keys=True,
            separators=(",", ":"),
        )
    return result


def _missing_archive_outcome(outcome: StepOutcome) -> StepOutcome:
    message = "Fleet OCI builder completed without declared archive output"
    return StepOutcome(
        argv=outcome.argv,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stdout=outcome.stdout,
        stderr=(outcome.stderr + f"\n{message}").strip(),
        duration_ms=outcome.duration_ms,
    )


def _write_oci_evidence(step: ResolvedOciBuildStep, paths: _OciPaths) -> None:
    payload = {
        "schema": "oh-goon/oci-build-evidence/v1",
        "archive": step.output.archive,
        "archive_sha256": _sha256(paths.archive),
        "archive_bytes": paths.archive.stat().st_size,
        "containerfile": step.containerfile,
        "context": step.context,
        "platform": step.policy.platform,
        "base_registries": [item.image_authority for item in step.registries],
        "run_network": step.policy.run_network,
        "registry_retries": step.policy.registry_retries,
        "source_date_epoch": step.policy.source_date_epoch,
        "tls_verify": step.policy.tls_verify,
    }
    publish_text_file(paths.metadata, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _contract_failure(message: str, outcome: StepOutcome) -> StepOutcome:
    return StepOutcome(
        argv=outcome.argv,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stdout=outcome.stdout,
        stderr=(outcome.stderr + f"\n{message}").strip(),
        duration_ms=outcome.duration_ms,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise FleetPlanInvalid(f"Fleet cannot hash OCI archive output: {exc}") from exc
    return digest.hexdigest()
