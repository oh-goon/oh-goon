from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.fleet_plan_validation import validate_resolved_plan
from oh_goon.core.models.agent_providers import AgentConnection
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_lock_models import LockedSkillSource
from oh_goon.core.models.fleet_plan_composition import (
    GoonOutputDefinition,
    PlanInputBinding,
    PlanInputDefinition,
    PlanStepInclusion,
    validate_template_contract,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    ExecutionPurpose,
    PlanExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    ParallelRepositoryExecution as ParallelRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    RepositoryExecutionMode as RepositoryExecutionMode,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    RepositoryExecutionPolicy as RepositoryExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    SerialRepositoryExecution as SerialRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_git import (
    GitAddAction as GitAddAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitAssertCleanAction as GitAssertCleanAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitCheckoutAction as GitCheckoutAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitCleanIgnoredAction as GitCleanIgnoredAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitCommitAction as GitCommitAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitCreateBranchAction as GitCreateBranchAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitDiffAction as GitDiffAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitNativeAction as GitNativeAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitPlanAction,
    GitResultContract,
)
from oh_goon.core.models.fleet_plan_git import (
    GitPlanOperation as GitPlanOperation,
)
from oh_goon.core.models.fleet_plan_kubernetes_steps import (
    KubernetesRolloutVerifyPlanStep,
    KubernetesSetImagePlanStep,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
)
from oh_goon.core.models.fleet_plan_observation_steps import (
    DependencyInventoryPlanStep,
    ResolvedDependencyInventoryStep,
)
from oh_goon.core.models.fleet_plan_oci_steps import (
    OciBuildPlanStep,
    OciPublishPlanStep,
    ResolvedOciBuildStep,
    ResolvedOciPublishStep,
)
from oh_goon.core.models.fleet_plan_oci_tag_steps import (
    OciTagAssertPlanStep,
    ResolvedOciTagAssertStep,
)
from oh_goon.core.models.fleet_plan_requirements import RepositoryRequirement
from oh_goon.core.models.fleet_plan_resources import (
    ExtensionCatalog as ExtensionCatalog,
)
from oh_goon.core.models.fleet_plan_resources import (
    ExtensionDeclaration as ExtensionDeclaration,
)
from oh_goon.core.models.fleet_plan_resources import (
    ManagedExtension as ManagedExtension,
)
from oh_goon.core.models.fleet_plan_resources import (
    ManagedToolRuntime as ManagedToolRuntime,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
    PlanStepCapabilities,
    PlanStepWorkspace,
    compile_simple_network_alias,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_CONTRACT_RETURN_CODE as PLAN_CONTRACT_RETURN_CODE,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE as PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE as PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_TIMEOUT_RETURN_CODE as PLAN_TIMEOUT_RETURN_CODE,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    MissingConfigPolicy,
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    PlanStreamOutput,
    StepCondition,
    StepFailurePolicy,
    StepLifecycle,
    StepRetryPolicy,
    _validate_published_outputs,
    _validate_success_codes,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PathExistsCondition as PathExistsCondition,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PathMissingCondition as PathMissingCondition,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    StepStatusCondition as StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_values import (
    PlanEnvDefinition,
    PlanStepImports,
    ResolvedEnvironmentBinding,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary.result_semantics import FailureImpact

FLEET_PLAN_CONTRACT_VERSION = 44
DEFAULT_PRE_COMMIT_REQUIREMENT = "pre-commit==4.6.2"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 3600.0


class UvExecutionEnvironment(ImmutableModel):
    """Backend-owned locked UV environment selected by a Goon.

    Authoring selects the repository-relative uv project plus dependency group.
    Lock enforcement, native materialization, cache lineage, and the concrete
    ``uv run`` argv remain resolved execution concerns.
    """

    project: str = Field(default=".", min_length=1, max_length=240)
    group: str = Field(
        pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}|\$\{\{\s*inputs\.[A-Za-z_][A-Za-z0-9_-]*\s*\}\})$"
    )

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Goon uv.project must be repository-relative")
        return path.as_posix()


class CommandPlanStep(ImmutableModel):
    _simple_network = model_validator(mode="before")(compile_simple_network_alias)
    kind: Literal["command"] = "command"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    timeout_seconds: float | None = Field(default=DEFAULT_COMMAND_TIMEOUT_SECONDS, gt=0, le=21600)
    uv: UvExecutionEnvironment | None = None
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput | PlanStreamOutput, ...] = Field(
        default_factory=tuple, max_length=16
    )

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    _outputs = field_validator("outputs")(_validate_published_outputs)


class AgentStepRequestedEffects(ImmutableModel):
    """SKILL-3B immutable authoring *request*, never a grant to the agent.

    Tool binaries use the original PlanStepCapabilities tool vocabulary and
    network egress contract. Recipients name *disclosure requests*, not network
    permissions. Repository writes require a separately admitted write scope.
    An untrusted Agent Step can NEVER request ambient host-unrestricted egress.
    """

    repository_access: Literal["read_only", "write"] = "read_only"
    recipients: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    # A concrete local model, frozen in the original AG-02 delivery identity.
    # A named recipient alone is not a model selection or a disclosure grant.
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)

    @model_validator(mode="after")
    def validate_agent_request(self) -> "AgentStepRequestedEffects":
        import re

        if self.recipients != tuple(sorted(set(self.recipients))):
            raise ValueError("Agent model recipients must be unique and sorted")
        for recipient in self.recipients:
            if (
                type(recipient) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", recipient) is None
            ):
                raise ValueError("Agent model recipients must be named canonical identities")
        if self.capabilities.egress.mode == "host_unrestricted":
            raise ValueError("Untrusted Agent Steps cannot request unrestricted host network")
        if self.capabilities.nested_strict_execution:
            raise ValueError("Untrusted Agent Steps cannot request nested strict execution")
        return self


class AgentPlanStep(ImmutableModel):
    """AG-02B typed *authoring* intent; not a runnable or admitted Step.

    There is intentionally no argv, command, credentials, endpoint, or shell.
    A future admitted Step must consume only an original-Resolver frozen capture
    through the existing Step sequencer after Enforcer/LACE/disclosure gates.
    """

    kind: Literal["agent"] = "agent"
    step_id: str = Field(min_length=1, max_length=80)
    harness: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    task: str = Field(min_length=1, max_length=4000)
    # Only a named earlier original Fleet Step output may become untrusted model feedback.
    feedback: str | None = Field(default=None, max_length=240)
    when: StepCondition | None = None
    timeout_seconds: float = Field(default=3600, gt=0, le=21600)
    requested_effects: AgentStepRequestedEffects = Field(default_factory=AgentStepRequestedEffects)


class GitPlanStep(ImmutableModel):
    _simple_network = model_validator(mode="before")(compile_simple_network_alias)
    kind: Literal["git"] = "git"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    action: GitPlanAction
    timeout_seconds: float | None = Field(gt=0, le=3600)
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=16)

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    _outputs = field_validator("outputs")(_validate_published_outputs)


class PreCommitPlanStep(ImmutableModel):
    _simple_network = model_validator(mode="before")(compile_simple_network_alias)
    kind: Literal["pre_commit"] = "pre_commit"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    if_missing: MissingConfigPolicy
    timeout_seconds: float | None = Field(gt=0, le=21600)
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=16)

    _outputs = field_validator("outputs")(_validate_published_outputs)


class ExtensionPlanStep(ImmutableModel):
    _simple_network = model_validator(mode="before")(compile_simple_network_alias)
    kind: Literal["extension"] = "extension"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    extension: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=120)
    args: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    timeout_seconds: float | None = Field(gt=0, le=21600)
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=16)

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    _outputs = field_validator("outputs")(_validate_published_outputs)


class CompiledGoonImportStep(ImmutableModel):
    """One compiled ordered Goon edge; never an authored Atomic-Goon Step."""

    kind: Literal["goon"] = "goon"
    phase: Literal["review", "adjudicate", "repair", "regression", "verification"] | None = None
    source_root: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("source_root")
    @classmethod
    def normalize_source_root(cls, value: str | None) -> str | None:
        return PurePosixPath(value).as_posix() if value is not None else None

    step_id: str = Field(min_length=1, max_length=40)
    goon: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=240,
    )

    @field_validator("goon")
    @classmethod
    def validate_static_goon_reference(cls, value: str) -> str:
        if "${{" in value:
            raise ValueError(
                "Compiled Goon references are static; inputs parameterize Goon contents, not Goon selection"
            )
        return value

    failure_impact: FailureImpact = FailureImpact.BLOCKING
    inputs: tuple[PlanInputBinding, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_input_names(self) -> CompiledGoonImportStep:
        names = [item.name for item in self.inputs]
        if len(names) != len(set(names)):
            raise ValueError("Compiled Goon input bindings must be unique")
        return self


PlanStep = Annotated[
    AgentPlanStep
    | CommandPlanStep
    | GitPlanStep
    | PreCommitPlanStep
    | OciBuildPlanStep
    | OciPublishPlanStep
    | OciTagAssertPlanStep
    | KubernetesSetImagePlanStep
    | KubernetesRolloutVerifyPlanStep
    | ExtensionPlanStep
    | DependencyInventoryPlanStep
    | CompiledGoonImportStep,
    Field(discriminator="kind"),
]


class CompiledExecutionTemplate(ImmutableModel):
    """Post-parse execution template shared by strict Plan/Goon compilers.

    This is not an authoring model. Plans compile ordered Goon selections into
    ``CompiledGoonImportStep`` edges; Atomic Goons compile executable Steps; Composite
    Goons compile Goon edges. Runtime resolution consumes this representation once and
    produces ``ResolvedFleetPlan``.
    """

    version: Literal[1] = 1
    authoring_kind: Literal["plan", "goon"] = "goon"
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    execution: PlanExecutionPolicy | None = None
    inputs: tuple[PlanInputDefinition, ...] = Field(default_factory=tuple, max_length=32)
    projected_inputs: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    ambiguous_inputs: tuple[str, ...] = ()
    env: tuple[PlanEnvDefinition, ...] = Field(default_factory=tuple, max_length=64)
    outputs: tuple[GoonOutputDefinition, ...] = Field(default_factory=tuple, max_length=32)
    inclusions: tuple[PlanStepInclusion, ...] = Field(default_factory=tuple, max_length=50)
    requirements: tuple[RepositoryRequirement, ...] = Field(default_factory=tuple, max_length=64)
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_step_ids(self) -> CompiledExecutionTemplate:
        validate_template_contract(
            self.inputs,
            self.outputs,
            self.inclusions,
            tuple(step.step_id for step in self.steps),
        )
        if len(self.projected_inputs) != len(set(self.projected_inputs)):
            raise ValueError("Plan projected input names must be unique")
        if self.inputs and self.projected_inputs:
            raise ValueError(
                "Compiled Plan cannot contain materialized inputs and pending projections"
            )
        env_names = [item.name for item in self.env]
        if len(env_names) != len(set(env_names)):
            raise ValueError("Fleet pipeline env names must be unique")
        if self.requirements:
            if self.execution is not None and self.execution.purpose is not ExecutionPurpose.CICD:
                raise ValueError(
                    "Repository requirements belong to Goon capabilities used by CI/CD execution"
                )
            identities = [item.model_dump_json() for item in self.requirements]
            if len(identities) != len(set(identities)):
                raise ValueError("Goon repository requirements must be unique")
        return self


class ResolvedCommandStep(CommandPlanStep):
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )

    @model_validator(mode="after")
    def validate_uv_selection(self):
        if self.uv is not None and (
            "${{" in self.uv.project
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.uv.group) is None
        ):
            raise ValueError("Resolved uv project and dependency group must be concrete")
        return self


class ResolvedLocalSearchAgentStep(ResolvedCommandStep):
    """Resolved read-only Agent intent for a fixed tool or provider harness.

    The immutable AG-02 packet is transported *separately* by the original
    attempt. This identity-only plan value never embeds captured repository or
    Skill bytes in journal/Plan projections. Unlike a generic command, it
    keeps ``kind=agent`` visible and forbids caller-supplied command arguments.
    """

    kind: Literal["agent"] = "agent"
    delivery_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    handoff_identity: str = Field(min_length=1, max_length=160)
    source_step_id: str = Field(min_length=1, max_length=80)
    feedback_reference: str | None = Field(default=None, max_length=240)
    # Original sequencer alone binds this from a published prior Step, never the model.
    feedback_value: str | None = Field(default=None, max_length=2048)
    harness: Literal[
        "local-search",
        "local-model-search",
        "local-model-review",
        "local-model-tools",
        "local-model-files",
        "local-model-skill-resources",
        "local-model-skill-loop",
        "local-model-proposal",
        "provider-review",
        "provider-adjudicate",
        "provider-proposal",
    ] = "local-search"
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    connection: AgentConnection | None = Field(default=None, exclude_if=lambda value: value is None)
    review_context: str | None = Field(
        default=None, max_length=65536, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_readonly_tool_shape(self) -> "ResolvedLocalSearchAgentStep":
        if self.harness.startswith("provider-") != (self.connection is not None):
            raise ValueError("provider Agent requires a frozen connection")
        if (
            self.argv != ("python",)
            or self.uv is not None
            or self.working_directory != "."
            or self.success_codes != (0,)
            or self.capabilities.egress.mode != "none"
            or self.capabilities.nested_strict_execution
            or self.capabilities.tools
            or self.imports.env
            or self.workspace.publish
        ):
            raise ValueError("local-search Agent Step must retain the fixed offline tool contract")
        if self.feedback_reference is not None and self.harness not in (
            "local-model-skill-loop",
            "local-model-proposal",
        ):
            raise ValueError(
                "only the read-only Skill loop and proposal harness accept failed-test feedback"
            )
        if (self.harness == "local-search" and self.model is not None) or (
            self.harness
            in (
                "local-model-search",
                "local-model-review",
                "local-model-tools",
                "local-model-files",
                "local-model-skill-resources",
                "local-model-skill-loop",
                "local-model-proposal",
            )
            and self.model is None
        ):
            raise ValueError("model recipient and selected model must match the Agent harness")
        return self


class ResolvedGitStep(ImmutableModel):
    """Canonical Git execution intent. Authoring conveniences compile to argv before admission."""

    kind: Literal["git"] = "git"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    argv: tuple[str, ...] = Field(min_length=1)
    result_contract: GitResultContract = GitResultContract.EXIT_CODE
    timeout_seconds: float | None = Field(gt=0, le=3600)
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=16)
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )

    _success_codes = field_validator("success_codes")(_validate_success_codes)
    _outputs = field_validator("outputs")(_validate_published_outputs)

    @model_validator(mode="after")
    def validate_result_contract(self) -> ResolvedGitStep:
        if self.result_contract is GitResultContract.CLEAN_WORKTREE and self.argv != (
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ):
            raise ValueError("clean-worktree Git result contract requires canonical status argv")
        return self


class ResolvedPreCommitStep(ImmutableModel):
    kind: Literal["pre_commit"] = "pre_commit"
    step_id: str
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    if_missing: MissingConfigPolicy
    timeout_seconds: float | None
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=16)
    runner_requirement: str = Field(
        default=DEFAULT_PRE_COMMIT_REQUIREMENT, min_length=1, max_length=120
    )
    runtime_id: str = Field(min_length=1)
    runtime_pyproject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_uv_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )

    _outputs = field_validator("outputs")(_validate_published_outputs)


class ResolvedExtensionStep(ImmutableModel):
    kind: Literal["extension"] = "extension"
    step_id: str
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    extension: str
    extension_id: str
    source: str
    revision: str
    project_name: str
    project_fingerprint: str
    pyproject_sha256: str
    uv_lock_sha256: str
    command: str
    entrypoint: str
    args: tuple[str, ...] = Field(default_factory=tuple)
    timeout_seconds: float | None
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=32)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=16)

    _outputs = field_validator("outputs")(_validate_published_outputs)
    _success_codes = field_validator("success_codes")(_validate_success_codes)


ResolvedPlanStep = Annotated[
    ResolvedLocalSearchAgentStep
    | ResolvedCommandStep
    | ResolvedGitStep
    | ResolvedPreCommitStep
    | ResolvedOciBuildStep
    | ResolvedOciPublishStep
    | ResolvedOciTagAssertStep
    | ResolvedKubernetesSetImageStep
    | ResolvedKubernetesRolloutVerifyStep
    | ResolvedExtensionStep
    | ResolvedDependencyInventoryStep,
    Field(discriminator="kind"),
]


def step_runtime_strings(step: ResolvedPlanStep) -> tuple[str, ...]:
    if isinstance(step, ResolvedCommandStep):
        return step.argv
    if isinstance(step, ResolvedExtensionStep):
        return step.args
    if isinstance(step, (ResolvedOciBuildStep, ResolvedOciPublishStep)):
        return ()
    if isinstance(step, ResolvedOciTagAssertStep):
        return (step.expected_digest,)
    if isinstance(step, ResolvedKubernetesSetImageStep):
        return (step.image,)
    if isinstance(step, ResolvedKubernetesRolloutVerifyStep):
        return (step.expected_generation, step.expected_image)
    if isinstance(step, ResolvedGitStep):
        return step.argv
    return ()


class ResolvedStepSource(ImmutableModel):
    """Non-semantic source position for one resolved concrete step."""

    step_id: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=240)
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class ResolvedImport(ImmutableModel):
    """One canonical shipped template import retained after authoring expansion.

    Every retained edge is a Goon import. Requirements remain scoped to the Goon
    that declared them and drive that import's repository applicability. They are
    never promoted into a second Plan-level admission owner.
    """

    import_id: str = Field(min_length=1, max_length=160)
    parent_import_id: str | None = Field(default=None, min_length=1, max_length=160)
    kind: Literal["goon"] = "goon"
    phase: Literal["review", "adjudicate", "repair", "regression", "verification"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    requirements: tuple[RepositoryRequirement, ...] = Field(default_factory=tuple, max_length=64)
    failure_impact: FailureImpact = FailureImpact.BLOCKING


class ResolvedFleetPlan(ImmutableModel):
    """Fully bound immutable execution plan.  Runtime never re-reads YAML/TOML."""

    # Retained Runs keep their original execution identity when reopened.
    contract_version: Literal[43, 44] = FLEET_PLAN_CONTRACT_VERSION
    plan_id: str
    name: str
    description: str = ""
    execution: PlanExecutionPolicy
    requirements: tuple[RepositoryRequirement, ...] = Field(default_factory=tuple, max_length=64)
    imports: tuple[ResolvedImport, ...] = Field(default_factory=tuple, max_length=100)
    steps: tuple[ResolvedPlanStep, ...] = Field(min_length=1, max_length=50)
    step_sources: tuple[ResolvedStepSource, ...] = Field(default_factory=tuple, max_length=50)
    skill_sources: tuple[LockedSkillSource, ...] = Field(
        default_factory=tuple, max_length=128, exclude_if=lambda value: not value
    )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        description: str,
        steps: tuple[ResolvedPlanStep, ...],
        execution: PlanExecutionPolicy,
        requirements: tuple[RepositoryRequirement, ...] = (),
        imports: tuple[ResolvedImport, ...] = (),
        step_sources: tuple[ResolvedStepSource, ...] = (),
        skill_sources: tuple[LockedSkillSource, ...] = (),
    ) -> ResolvedFleetPlan:
        payload = {
            "contract_version": FLEET_PLAN_CONTRACT_VERSION,
            "name": name,
            "description": description,
            "execution": execution.model_dump(mode="json", exclude_computed_fields=True),
            "requirements": [
                item.model_dump(mode="json", exclude_computed_fields=True) for item in requirements
            ],
            "imports": [
                item.model_dump(mode="json", exclude_computed_fields=True) for item in imports
            ],
            "steps": [step.model_dump(mode="json", exclude_computed_fields=True) for step in steps],
            **(
                {"skill_sources": [item.model_dump(mode="json") for item in skill_sources]}
                if skill_sources
                else {}
            ),
        }
        return cls(
            plan_id=stable_id("fleet-plan", payload),
            name=name,
            description=description,
            execution=execution,
            requirements=requirements,
            imports=imports,
            steps=steps,
            step_sources=step_sources,
            skill_sources=skill_sources,
        )

    @model_validator(mode="after")
    def validate_plan_id(self) -> ResolvedFleetPlan:
        validate_resolved_plan(self)
        return self
