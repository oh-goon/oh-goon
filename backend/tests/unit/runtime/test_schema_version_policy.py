from __future__ import annotations

from oh_goon.runtime.schema_version_policy import (
    DurableSchemaDisposition,
    DurableSchemaPolicy,
)


def test_durable_schema_policy_classifies_current_direction_only() -> None:
    policy = DurableSchemaPolicy(name="example", current=7)

    assert policy.classify(0) is DurableSchemaDisposition.UNVERSIONED
    assert policy.classify(6) is DurableSchemaDisposition.STALE
    assert policy.classify(7) is DurableSchemaDisposition.CURRENT
    assert policy.classify(8) is DurableSchemaDisposition.FUTURE
