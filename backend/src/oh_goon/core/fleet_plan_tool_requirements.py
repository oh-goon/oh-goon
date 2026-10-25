from __future__ import annotations

from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedDependencyInventoryStep,
    ResolvedExtensionStep,
    ResolvedFleetPlan,
    ResolvedGitStep,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
    ResolvedOciBuildStep,
    ResolvedOciPublishStep,
    ResolvedOciTagAssertStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)

_SHELL_EXECUTION_TOOLS = frozenset({"bash", "dash", "sh"})
# Fixed sandbox-local support for an explicitly admitted shell. These tools do not
# grant host filesystem/network authority: they operate inside the already-bounded
# runtime root and each executable is still frozen and evidenced before launch.
SHELL_RUNTIME_SUPPORT_TOOLS = (
    "cat",
    "chmod",
    "cp",
    "cut",
    "dirname",
    "find",
    "grep",
    "ln",
    "mkdir",
    "mv",
    "readlink",
    "rm",
    "sha256sum",
    "sleep",
    "true",
)


def shell_runtime_support_tool_names(tool_names: tuple[str, ...]) -> tuple[str, ...]:
    """Return fixed sandbox support only when a shell is explicitly admitted."""

    return SHELL_RUNTIME_SUPPORT_TOOLS if _SHELL_EXECUTION_TOOLS.intersection(tool_names) else ()


def step_execution_tool(step: ResolvedPlanStep) -> str | None:
    """Return the primary executable selected by a resolved step.

    Managed pre-commit runtimes, built-in in-process observations, and repository-relative
    executables do not consume the executor image/host toolchain. Typed integrations name
    their implementation tool here so authoring never duplicates that fact in YAML.
    Native Agents return their frozen selected path; host-tool names are projected
    separately by ``step_execution_tool_names`` and never resolve that path again.
    """

    if isinstance(step, ResolvedAgentStep):
        if step.workload.harness == "native":
            return step.workload.native_agent.definition.executable
        return "python"
    if isinstance(step, ResolvedCommandStep):
        executable = step.argv[0]
        return executable if "/" not in executable and "\\" not in executable else None
    if isinstance(step, ResolvedGitStep):
        return "git"
    if isinstance(step, ResolvedOciBuildStep):
        return "buildah"
    if isinstance(step, (ResolvedOciPublishStep, ResolvedOciTagAssertStep)):
        return "skopeo"
    if isinstance(step, (ResolvedKubernetesSetImageStep, ResolvedKubernetesRolloutVerifyStep)):
        return "curl"
    if isinstance(step, ResolvedExtensionStep):
        return "uv"
    if isinstance(step, ResolvedPreCommitStep):
        return None
    if isinstance(step, ResolvedDependencyInventoryStep):
        return None
    raise TypeError(f"Unsupported resolved Fleet step: {type(step).__name__}")


def step_execution_tool_names(step: ResolvedPlanStep) -> tuple[str, ...]:
    """Return the complete frozen host-tool authority for one resolved Step."""

    names = set(step.capabilities.tools)
    if isinstance(step, ResolvedAgentStep) and step.workload.harness == "native":
        # Native selection already owns the exact path and bytes. Python supports
        # producer evidence and the isolated relay; it does not select the agent.
        names.add("python")
        return tuple(sorted(names))
    primary = step_execution_tool(step)
    if primary is not None and "/" not in primary and "\\" not in primary:
        names.add(primary)
    if isinstance(step, ResolvedOciBuildStep):
        # Buildah's OCI isolation executes RUN instructions through this fixed
        # companion. It must be frozen with Buildah, never discovered at launch.
        names.add("crun")
    return tuple(sorted(names))


def step_runtime_tool_names(step: ResolvedPlanStep) -> tuple[str, ...]:
    """Return frozen executables needed by the step, including shell runtime support."""

    authority = step_execution_tool_names(step)
    return tuple(sorted(set(authority) | set(shell_runtime_support_tool_names(authority))))


def required_execution_tool_names(plan: ResolvedFleetPlan) -> tuple[str, ...]:
    """Return authority-bearing tools explicitly required by a resolved run."""

    return tuple(sorted({tool for step in plan.steps for tool in step_execution_tool_names(step)}))


def required_runtime_tool_names(plan: ResolvedFleetPlan) -> tuple[str, ...]:
    """Return the exact frozen toolchain, including fixed shell runtime support."""

    return tuple(sorted({tool for step in plan.steps for tool in step_runtime_tool_names(step)}))
