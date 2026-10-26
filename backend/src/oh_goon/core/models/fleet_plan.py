from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, PrivateAttr, TypeAdapter, field_validator, model_validator

from oh_goon.core.fleet_plan_validation import validate_resolved_plan
from oh_goon.core.models.agent_providers import AgentConnection, ResolvedNativeAgent
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_lock_models import LockedSkillSource
from oh_goon.core.models.fleet_plan_composition import (
    GoonOutputDefinition as GoonOutputDefinition,
)
from oh_goon.core.models.fleet_plan_composition import (
    PlanInputBinding as PlanInputBinding,
)
from oh_goon.core.models.fleet_plan_composition import (
    PlanInputDefinition as PlanInputDefinition,
)
from oh_goon.core.models.fleet_plan_composition import (
    PlanStepInclusion as PlanStepInclusion,
)
from oh_goon.core.models.fleet_plan_composition import (
    validate_template_contract as validate_template_contract,
)
from oh_goon.core.models.fleet_plan_diagnostics import PlanConfigurationEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    ExecutionPurpose as ExecutionPurpose,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    ParallelRepositoryExecution as ParallelRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    PlanExecutionPolicy as PlanExecutionPolicy,
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
    GitPlanAction as GitPlanAction,
)
from oh_goon.core.models.fleet_plan_git import (
    GitPlanOperation as GitPlanOperation,
)
from oh_goon.core.models.fleet_plan_git import (
    GitResultContract as GitResultContract,
)
from oh_goon.core.models.fleet_plan_kubernetes_steps import (
    KubernetesRolloutVerifyPlanStep as KubernetesRolloutVerifyPlanStep,
)
from oh_goon.core.models.fleet_plan_kubernetes_steps import (
    KubernetesSetImagePlanStep as KubernetesSetImagePlanStep,
)
from oh_goon.core.models.fleet_plan_kubernetes_steps import (
    ResolvedKubernetesRolloutVerifyStep as ResolvedKubernetesRolloutVerifyStep,
)
from oh_goon.core.models.fleet_plan_kubernetes_steps import (
    ResolvedKubernetesSetImageStep as ResolvedKubernetesSetImageStep,
)
from oh_goon.core.models.fleet_plan_observation_steps import (
    DependencyInventoryPlanStep as DependencyInventoryPlanStep,
)
from oh_goon.core.models.fleet_plan_observation_steps import (
    ResolvedDependencyInventoryStep as ResolvedDependencyInventoryStep,
)
from oh_goon.core.models.fleet_plan_oci_steps import (
    OciBuildPlanStep as OciBuildPlanStep,
)
from oh_goon.core.models.fleet_plan_oci_steps import (
    OciPublishPlanStep as OciPublishPlanStep,
)
from oh_goon.core.models.fleet_plan_oci_steps import (
    ResolvedOciBuildStep as ResolvedOciBuildStep,
)
from oh_goon.core.models.fleet_plan_oci_steps import (
    ResolvedOciPublishStep as ResolvedOciPublishStep,
)
from oh_goon.core.models.fleet_plan_oci_tag_steps import (
    OciTagAssertPlanStep as OciTagAssertPlanStep,
)
from oh_goon.core.models.fleet_plan_oci_tag_steps import (
    ResolvedOciTagAssertStep as ResolvedOciTagAssertStep,
)
from oh_goon.core.models.fleet_plan_requirements import (
    UV_PROJECT_SELECTION_SWITCHES,
    RepositoryRequirement,
    UvLockMode,
)
from oh_goon.core.models.fleet_plan_resources import CapturedProjectSource
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
    PlanStepCache as PlanStepCache,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCapabilities as PlanStepCapabilities,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepWorkspace as PlanStepWorkspace,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    compile_simple_network_alias as compile_simple_network_alias,
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
    MissingConfigPolicy as MissingConfigPolicy,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PathExistsCondition as PathExistsCondition,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PathMissingCondition as PathMissingCondition,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PlanArtifactDeclaration as PlanArtifactDeclaration,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PlanPublishedOutput as PlanPublishedOutput,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PlanStreamOutput as PlanStreamOutput,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    StepCondition as StepCondition,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    StepFailurePolicy as StepFailurePolicy,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    StepLifecycle as StepLifecycle,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    StepRetryPolicy as StepRetryPolicy,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    StepStatusCondition as StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    _validate_published_outputs,
    _validate_success_codes,
)
from oh_goon.core.models.fleet_plan_values import (
    PlanEnvDefinition as PlanEnvDefinition,
)
from oh_goon.core.models.fleet_plan_values import (
    PlanStepImports as PlanStepImports,
)
from oh_goon.core.models.fleet_plan_values import (
    ResolvedEnvironmentBinding as ResolvedEnvironmentBinding,
)
from oh_goon.core.models.git_sources import (
    GitSourceSelection,
    LocalApplicationSource,
    LockedExecutionSource,
)
from oh_goon.core.models.provenance import MAX_SOURCE_LOCATION_LENGTH
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary.result_semantics import FailureImpact

FLEET_PLAN_CONTRACT_VERSION: Literal[47] = 47
_NATIVE_AGENT_ADAPTER = TypeAdapter(ResolvedNativeAgent)
DEFAULT_PRE_COMMIT_REQUIREMENT = "pre-commit==4.6.2"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 3600.0


class UvExecutionEnvironment(ImmutableModel):
    """One explicit native UV selection, materialized by the authoring compiler.

    Authoring selects the repository-relative uv project plus dependency group.
    Lock enforcement, native materialization, cache lineage, and the concrete
    ``uv run`` argv remain resolved execution concerns.
    """

    lock_mode: UvLockMode
    project: str = Field(default=".", min_length=1, max_length=240)
    source: Literal["repository", "captured"] = Field(
        default="repository", exclude_if=lambda value: value == "repository"
    )
    group: str | None = Field(
        default=None,
        pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}|\$\{\{\s*inputs\.[A-Za-z_][A-Za-z0-9_-]*\s*\}\})$",
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


AgentResultRole = Literal["report", "review", "adjudication", "proposal"]
MAX_AGENT_TASK_CHARS = 4000


class AgentPlanStep(ImmutableModel):
    """AG-02B typed *authoring* intent; not a runnable or admitted Step.

    There is intentionally no argv, command, credentials, endpoint, or shell.
    A future admitted Step must consume only an original-Resolver frozen capture
    through the existing Step sequencer after Enforcer/LACE/disclosure gates.
    """

    kind: Literal["agent"] = "agent"
    step_id: str = Field(min_length=1, max_length=80)
    harness: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    role: AgentResultRole = Field(default="report", exclude_if=lambda value: value == "report")
    task: str = Field(min_length=1, max_length=MAX_AGENT_TASK_CHARS)
    # Only a named earlier original Fleet Step output may become untrusted model feedback.
    feedback: str | None = Field(default=None, max_length=240)
    when: StepCondition | None = None
    timeout_seconds: float = Field(default=3600, gt=0, le=21600)
    requested_effects: AgentStepRequestedEffects = Field(default_factory=AgentStepRequestedEffects)

    @field_validator("harness")
    @classmethod
    def reject_retired_skill_loop(cls, value: str) -> str:
        if value == "local-model-skill-loop":
            raise ValueError(
                "local-model-skill-loop was removed; re-author with a selected native "
                "Agent for reasoning, or a bounded Skill-resource/proposal workload"
            )
        return value


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
    git_source: GitSourceSelection | None = None

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
        if self.source_root is not None and self.git_source is not None:
            raise ValueError("Select one explicit Goon source")
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
    runtime: Literal["python"] | None = None
    application: LocalApplicationSource | GitSourceSelection | None = None
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
        if self.application is not None and any(
            not isinstance(step, CommandPlanStep) for step in self.steps
        ):
            raise ValueError("Application sources belong to Atomic Goon command Steps")
        if self.application is not None:
            for step in self.steps:
                assert isinstance(step, CommandPlanStep)
                if step.workspace.source.kind != "repository" or step.workspace.publish:
                    raise ValueError(
                        "Application source cannot be replaced or published as a Step workspace"
                    )
                paths = (
                    step.working_directory,
                    *(getattr(output, "path", "") for output in (*step.outputs, *step.artifacts)),
                )
                if any("${" in path for path in paths):
                    raise ValueError(
                        "Captured application working and output paths must be literal"
                    )
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
    project_source: CapturedProjectSource | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )

    @model_validator(mode="after")
    def validate_uv_selection(self) -> ResolvedCommandStep:
        if self.uv is not None and self.argv[:3] != ("uv", "run", f"--{self.uv.lock_mode}"):
            raise ValueError("Resolved uv argv must retain its selected native lock mode")
        captured = self.project_source is not None
        if self.uv is not None and (self.uv.source == "captured") != captured:
            raise ValueError("Captured uv execution requires its exact captured project source")
        package = self.project_source is not None and self.project_source.application is None
        if package and (self.uv is None or self.uv.source != "captured"):
            raise ValueError("Python Goon package requires its captured uv environment")
        if package and self.uv is not None and self.uv.project != ".":
            raise ValueError("Python Goon runtime selects its package root")
        if (
            captured
            and not package
            and (self.workspace.source.kind != "repository" or self.workspace.publish)
        ):
            raise ValueError(
                "Application source cannot be replaced or published as a Step workspace"
            )
        if self.uv is not None and any(
            binding.name in UV_PROJECT_SELECTION_SWITCHES for binding in self.environment
        ):
            raise ValueError(
                "Goon uv selection owns native project and lock switches; remove those imports"
            )
        if self.uv is not None and (
            "${{" in self.uv.project
            or (
                self.uv.group is not None
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.uv.group) is None
            )
        ):
            raise ValueError("Resolved uv project and dependency group must be concrete")
        return self


class ResolvedSearchAgentWorkload(ImmutableModel):
    """Model-free deterministic search over the exact frozen source."""

    harness: Literal["local-search"] = "local-search"


class ResolvedBuiltinAgentWorkload(ImmutableModel):
    """An explicitly selected bounded local-model protocol and model."""

    harness: Literal[
        "local-model-search",
        "local-model-review",
        "local-model-tools",
        "local-model-files",
        "local-model-skill-resources",
        "local-model-proposal",
    ]
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


class ResolvedProviderAgentWorkload(ImmutableModel):
    """A direct, bounded provider request with its frozen connection."""

    harness: Literal["provider-review", "provider-adjudicate", "provider-proposal"]
    connection: AgentConnection
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")

    @model_validator(mode="after")
    def validate_selected_profile(self) -> ResolvedProviderAgentWorkload:
        if self.model not in (None, self.connection.profile):
            raise ValueError("provider model request differs from the frozen connection profile")
        return self


class ResolvedNativeAgentWorkload(ImmutableModel):
    """Native reasoning/tool ownership and native-owned model configuration."""

    harness: Literal["native"] = "native"
    native_agent: ResolvedNativeAgent
    role: AgentResultRole = "report"


ResolvedAgentWorkload = Annotated[
    ResolvedSearchAgentWorkload
    | ResolvedBuiltinAgentWorkload
    | ResolvedProviderAgentWorkload
    | ResolvedNativeAgentWorkload,
    Field(discriminator="harness"),
]


class ResolvedAgentStep(ImmutableModel):
    """Neutral immutable Agent intent under the original Step lifecycle.

    The workload variant owns only its relevant selection. Source and Skill
    bytes travel separately in the original attempt's delivery packet; neither
    the packet nor this identity envelope grants execution authority.
    """

    kind: Literal["agent"] = "agent"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    timeout_seconds: float = Field(gt=0, le=21600)
    working_directory: Literal["."] = "."
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
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )
    workload: ResolvedAgentWorkload
    _outputs = field_validator("outputs")(_validate_published_outputs)
    delivery_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    handoff_identity: str = Field(min_length=1, max_length=160)
    source_step_id: str = Field(min_length=1, max_length=80)
    feedback_reference: str | None = Field(default=None, max_length=240)
    # Original sequencer alone binds this from a published prior Step, never the model.
    feedback_value: str | None = Field(default=None, max_length=2048)
    review_context: str | None = Field(
        default=None, max_length=65536, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_execution_envelope(self) -> "ResolvedAgentStep":
        if (
            self.success_codes != (0,)
            or self.capabilities.egress.mode != "none"
            or self.capabilities.nested_strict_execution
            or self.capabilities.tools
            or self.imports.env
            or self.workspace.publish
        ):
            raise ValueError("Agent Step must retain its admitted fixed effect envelope")
        if self.feedback_reference is not None and self.workload.harness not in (
            "local-model-proposal",
        ):
            raise ValueError("only the read-only proposal harness accepts failed-test feedback")
        if self.feedback_value is not None and self.feedback_reference is None:
            raise ValueError("Agent feedback requires its original published output reference")
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
    ResolvedAgentStep
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
    source: str = Field(min_length=1, max_length=MAX_SOURCE_LOCATION_LENGTH)
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

    contract_version: Literal[47] = FLEET_PLAN_CONTRACT_VERSION
    _project_archives: dict[str, bytes] = PrivateAttr(default_factory=dict)

    @field_validator("contract_version", mode="before")
    @classmethod
    def require_current_execution_contract(cls, value: object) -> object:
        if value != FLEET_PLAN_CONTRACT_VERSION:
            raise ValueError(
                "Incompatible resolved Plan contract; re-resolve and re-lock authoring "
                "with the current runtime before admission"
            )
        return value

    plan_id: str
    name: str
    description: str = ""
    execution: PlanExecutionPolicy
    native_agent: ResolvedNativeAgent | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    requirements: tuple[RepositoryRequirement, ...] = Field(default_factory=tuple, max_length=64)
    imports: tuple[ResolvedImport, ...] = Field(default_factory=tuple, max_length=100)
    steps: tuple[ResolvedPlanStep, ...] = Field(min_length=1, max_length=50)
    step_sources: tuple[ResolvedStepSource, ...] = Field(default_factory=tuple, max_length=50)
    configuration: PlanConfigurationEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    skill_sources: tuple[LockedSkillSource, ...] = Field(
        default_factory=tuple, max_length=128, exclude_if=lambda value: not value
    )
    execution_sources: tuple[LockedExecutionSource, ...] = Field(
        default=(), max_length=128, exclude_if=lambda value: not value
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
        execution_sources: tuple[LockedExecutionSource, ...] = (),
        configuration: PlanConfigurationEvidence | None = None,
        native_agent: ResolvedNativeAgent | None = None,
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
                {"native_agent": _NATIVE_AGENT_ADAPTER.dump_python(native_agent, mode="json")}
                if native_agent is not None
                else {}
            ),
            **(
                {"skill_sources": [item.model_dump(mode="json") for item in skill_sources]}
                if skill_sources
                else {}
            ),
            **(
                {"execution_sources": [item.model_dump(mode="json") for item in execution_sources]}
                if execution_sources
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
            execution_sources=execution_sources,
            configuration=configuration,
            native_agent=native_agent,
        )

    @model_validator(mode="after")
    def validate_plan_id(self) -> ResolvedFleetPlan:
        validate_resolved_plan(self)
        return self
