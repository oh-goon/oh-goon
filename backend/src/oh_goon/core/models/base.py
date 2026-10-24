# Pydantic computed fields retain @property for static attribute typing. Mypy does
# not support that decorator stack; only those exact @computed_field lines use
# its documented prop-decorator exception. Other diagnostics remain enabled.
# https://docs.pydantic.dev/latest/api/fields/#pydantic.fields.computed_field
from pydantic import BaseModel, ConfigDict


class ImmutableModel(BaseModel):
    """Domain value/fact base: validated once, then never mutated in place."""

    model_config = ConfigDict(extra="forbid", frozen=True)
