from __future__ import annotations

from pydantic import AwareDatetime, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import PolicySource, WorkspaceMode, WorkspaceScope


class StrictPolicyModel(ImmutableModel):
    pass


class PolicyValues(StrictPolicyModel):
    require_remote: bool
    test_command: str
    require_audit: bool
    fail_on_audit: bool
    audit_timeout_seconds: int
    git_timeout_seconds: int
    malware_check: bool
    workspace_mode: WorkspaceMode
    workspace_scope: WorkspaceScope
    require_python_pin: bool


class PolicySources(StrictPolicyModel):
    require_remote: PolicySource
    test_command: PolicySource
    require_audit: PolicySource
    fail_on_audit: PolicySource
    audit_timeout_seconds: PolicySource
    git_timeout_seconds: PolicySource
    malware_check: PolicySource
    workspace_mode: PolicySource
    workspace_scope: PolicySource
    require_python_pin: PolicySource


class PolicySnapshot(StrictPolicyModel):
    captured_at: AwareDatetime
    values: PolicyValues
    sources: PolicySources

    @computed_field
    @property
    def policy_id(self) -> str:
        return stable_id(
            "policy",
            {
                "values": self.values.model_dump(mode="json", exclude_computed_fields=True),
                "sources": self.sources.model_dump(mode="json", exclude_computed_fields=True),
            },
        )

    @model_validator(mode="after")
    def validate_source_semantics(self) -> PolicySnapshot:
        self._validate_source_semantics()
        return self

    def _validate_source_semantics(self) -> None:
        if self.sources.workspace_scope is not PolicySource.DERIVED:
            raise ValueError("workspace_scope policy source must be DERIVED")
