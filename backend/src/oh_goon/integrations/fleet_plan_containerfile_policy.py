from __future__ import annotations

import re
import shlex
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_oci_steps import ResolvedOciBuildStep
from oh_goon.core.path_authority import ContainedPathSpace, RepositoryPathSpace
from oh_goon.core.product_identity import stable_id

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REMOTE_SOURCE = re.compile(r"^(?:https?|git|ssh)://|^git@", re.IGNORECASE)


def validate_oci_build_repository_policy(
    root: Path,
    step: ResolvedOciBuildStep,
) -> tuple[str, ...]:
    """Return deterministic pre-effect issues owned by one resolved OCI build step."""

    issues: list[str] = []
    space = RepositoryPathSpace.from_root(root)
    try:
        context = space.resolve(
            step.context,
            label=f"Fleet OCI build context for {step.step_id}",
            kind="directory",
            require_exists=True,
        )
    except FleetPlanInvalid as exc:  # path authority owns the exact diagnostic
        return (str(exc),)
    try:
        containerfile = space.resolve(
            step.containerfile,
            label=f"Fleet OCI Containerfile for {step.step_id}",
            kind="file",
            require_exists=True,
        )
    except FleetPlanInvalid as exc:
        return (str(exc),)
    try:
        ContainedPathSpace.from_root(context).relative(
            containerfile, label=f"Fleet OCI Containerfile for {step.step_id}"
        )
    except FleetPlanInvalid:
        issues.append("Fleet OCI Containerfile must be inside the declared build context")
        return tuple(issues)
    try:
        text = containerfile.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return (f"Fleet OCI Containerfile cannot be read as UTF-8: {exc}",)

    authorities = {item.image_authority for item in step.registries}
    stages: set[str] = set()
    for line_number, instruction in _logical_instructions(text):
        opcode, _, payload = instruction.partition(" ")
        opcode = opcode.upper()
        payload = payload.strip()
        if opcode == "FROM":
            issue, alias = _validate_from(payload, authorities, stages)
            if issue:
                issues.append(f"OCI step {step.step_id} Containerfile line {line_number}: {issue}")
            if alias:
                stages.add(alias)
        elif opcode == "ADD":
            issue = _validate_add(payload)
            if issue:
                issues.append(f"OCI step {step.step_id} Containerfile line {line_number}: {issue}")
        elif opcode == "COPY":
            issue = _validate_copy(payload, stages)
            if issue:
                issues.append(f"OCI step {step.step_id} Containerfile line {line_number}: {issue}")
        elif opcode == "RUN" and "--mount=" in payload:
            issues.append(
                f"OCI step {step.step_id} Containerfile line {line_number}: "
                "RUN --mount is not admitted by OCI build v1"
            )
    return tuple(issues)


def oci_build_repository_policy_id(step: ResolvedOciBuildStep) -> str:
    """Return the canonical identity of the repository policy intrinsic to one OCI build."""

    return stable_id(
        "oci-build-repository-policy",
        {
            "step_id": step.step_id,
            "containerfile": step.containerfile,
            "context": step.context,
            "registries": [item.model_dump(mode="json") for item in step.registries],
        },
    )


def _logical_instructions(text: str) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    pending = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start = number
        fragment = stripped[:-1].rstrip() if stripped.endswith("\\") else stripped
        pending = f"{pending} {fragment}".strip()
        if stripped.endswith("\\"):
            continue
        if pending:
            result.append((start, pending))
        pending = ""
    if pending:
        result.append((start, pending))
    return tuple(result)


def _validate_from(
    payload: str,
    authorities: set[str],
    stages: set[str],
) -> tuple[str | None, str | None]:
    if "$" in payload:
        return (
            "FROM cannot contain variable expansion; base authority must be statically provable",
            None,
        )
    try:
        parts = shlex.split(payload)
    except ValueError as exc:
        return f"FROM is not parseable: {exc}", None
    while parts and parts[0].startswith("--"):
        parts.pop(0)
    if not parts:
        return "FROM is missing its base image", None
    image = parts[0]
    alias: str | None = None
    if len(parts) >= 3 and parts[1].upper() == "AS":
        alias = parts[2]
    elif len(parts) != 1:
        return "FROM must use '<image> [AS stage]' after fixed options", None
    if alias is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", alias):
        return "FROM stage alias is invalid", None
    if image == "scratch" or image in stages or image.isdigit():
        return None, alias
    authority, sep, remainder = image.partition("/")
    if not sep or authority not in authorities:
        allowed = ", ".join(sorted(authorities)) or "none"
        return (
            f"base image registry {authority!r} is not declared; allowed registries: {allowed}",
            None,
        )
    name, at, digest = remainder.rpartition("@")
    if not at or not name or _DIGEST.fullmatch(digest) is None:
        return "external base images must be digest-pinned as registry/path@sha256:<64 hex>", None
    if ":" in name.rsplit("/", 1)[-1]:
        return "external base images cannot combine a mutable tag with the required digest", None
    return None, alias


def _validate_add(payload: str) -> str | None:
    try:
        parts = shlex.split(payload)
    except ValueError as exc:
        return f"ADD is not parseable: {exc}"
    sources = [item for item in parts[:-1] if not item.startswith("--")]
    if any(_REMOTE_SOURCE.search(source) for source in sources):
        return "remote ADD sources are forbidden; build context is the only admitted source"
    return None


def _validate_copy(payload: str, stages: set[str]) -> str | None:
    try:
        parts = shlex.split(payload)
    except ValueError as exc:
        return f"COPY is not parseable: {exc}"
    for part in parts:
        if not part.startswith("--from="):
            continue
        source = part.split("=", 1)[1]
        if source not in stages and not source.isdigit():
            return "COPY --from may reference only an earlier local build stage"
    return None
