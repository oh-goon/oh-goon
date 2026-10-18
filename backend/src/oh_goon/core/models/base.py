from pydantic import BaseModel, ConfigDict


class ImmutableModel(BaseModel):
    """Domain value/fact base: validated once, then never mutated in place."""

    model_config = ConfigDict(extra="forbid", frozen=True)
