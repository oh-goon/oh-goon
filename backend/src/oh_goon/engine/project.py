from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from oh_goon.core.engine_policy_defaults import DEFAULT_ENGINE_ENV
from oh_goon.core.models.operation import (
    ProjectDeclarationEvidence,
    ProjectDeclarationUnavailable,
    PythonRuntimeResult,
)
from oh_goon.core.models.policy import PolicySnapshot, PolicySources, PolicyValues
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import PolicySource, WorkspaceMode, WorkspaceScope
from oh_goon.integrations.hashmarks import (
    HashmarksSourceInputError,
    observe_python_project_declarations,
)


def read_toml(path: Path) -> dict[str, Any]:
    """Read the selected extension's native launch configuration, not repository evidence."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def read_pyproject(repository: Path) -> dict[str, Any]:
    return read_toml(repository / "pyproject.toml")


def project_python(repository: Path) -> PythonRuntimeResult:
    requires = None
    observed: ProjectDeclarationEvidence | ProjectDeclarationUnavailable
    try:
        evidence = observe_python_project_declarations(repository)
    except HashmarksSourceInputError as error:
        # Explanatory metadata cannot erase native uv's structured readiness
        # rejection. Missing/incompatible producer runtime still fails closed.
        observed = ProjectDeclarationUnavailable(
            producer_implementation_identity=error.producer_implementation_identity,
            reason=str(error),
        )
    else:
        packet = evidence.packet()
        project_group = None
        for group in packet["declarations"]["groups"]:
            if group["concept"]["kind"] == "python-project-declaration":
                project_group = group
                for declaration in group["declarations"]:
                    requires = declaration["value"]["declared"].get("requires-python")
        observed = ProjectDeclarationEvidence(
            producer_implementation_identity=evidence.producer_implementation_identity,
            observation_identity=packet["observation_identity"],
            repository=packet["repository"],
            provider=packet["providers"][0],
            declaration_group=project_group,
        )
    # This is a native local runtime pin input. It neither resolves Python nor
    # establishes a repository/project relationship.
    pin_path = repository / ".python-version"
    pin = None
    if pin_path.is_file():
        lines = pin_path.read_text(encoding="utf-8").strip().splitlines()
        pin = lines[0].strip() if lines else None
    return PythonRuntimeResult(
        requires=requires,
        pin=pin or None,
        project_declaration=observed,
    )


def policy_snapshot(repository: Path) -> PolicySnapshot:
    del repository  # Native uv owns project/index/resolution configuration semantics.
    env = {**DEFAULT_ENGINE_ENV, **os.environ}
    workspace_mode = WorkspaceMode(env.get("OH_GOON_WORKSPACE_MODE", "root"))
    workspace_scope = (
        WorkspaceScope.ALL_PACKAGES
        if workspace_mode is WorkspaceMode.ALL
        else WorkspaceScope.ROOT_ONLY
    )
    values = PolicyValues(
        require_remote=_bool(env.get("OH_GOON_REQUIRE_REMOTE", "true")),
        test_command=env.get("OH_GOON_TEST_COMMAND", "uv run --frozen pytest"),
        require_audit=_bool(env.get("OH_GOON_REQUIRE_AUDIT", "false")),
        fail_on_audit=_bool(env.get("OH_GOON_FAIL_ON_AUDIT", "true")),
        audit_timeout_seconds=int(env.get("OH_GOON_AUDIT_TIMEOUT", "20")),
        git_timeout_seconds=int(env.get("OH_GOON_GIT_TIMEOUT", "30")),
        malware_check=_bool(env.get("OH_GOON_MALWARE_CHECK", "false")),
        workspace_mode=workspace_mode,
        workspace_scope=workspace_scope,
        require_python_pin=_bool(env.get("OH_GOON_REQUIRE_PYTHON_PIN", "false")),
    )
    fleet = PolicySource.FLEET_POLICY
    sources = PolicySources(
        require_remote=fleet,
        test_command=fleet,
        require_audit=fleet,
        fail_on_audit=fleet,
        audit_timeout_seconds=fleet,
        git_timeout_seconds=fleet,
        malware_check=fleet,
        workspace_mode=fleet,
        workspace_scope=PolicySource.DERIVED,
        require_python_pin=fleet,
    )
    return PolicySnapshot(captured_at=utc_now(), values=values, sources=sources)


def _bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}
