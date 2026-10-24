from pydantic import AwareDatetime, BaseModel, Field
from pydantic.config import JsonDict

from oh_goon.core import engine_policy_defaults as policy_defaults


def _project_default_exclusion_reason(schema: JsonDict) -> None:
    schema["default"] = policy_defaults.default_exclusion_reason()


class ExcludeRepositoryRequest(BaseModel):
    repository: str
    expected_repository_instance_id: str = Field(min_length=1)
    reason: str = Field(
        default_factory=policy_defaults.default_exclusion_reason,
        max_length=500,
        json_schema_extra=_project_default_exclusion_reason,
    )


class IncludeRepositoryRequest(BaseModel):
    repository: str
    expected_repository_instance_id: str = Field(min_length=1)


class ExclusionRecord(BaseModel):
    path: str
    reason: str
    excluded_at: AwareDatetime
