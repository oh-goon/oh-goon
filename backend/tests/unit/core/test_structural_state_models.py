from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oh_goon.core.models.repository import (
    ExcludedRepositoryCatalogEntry,
    IncludedRepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.vocabulary import FleetScope
from tests.factories.schedules import schedule_create


def test_repository_scope_variants_cannot_carry_each_others_state(tmp_path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    now = datetime.now(UTC)

    with pytest.raises(ValidationError):
        IncludedRepositoryCatalogEntry(
            scope=FleetScope.INCLUDED,
            repository=repository,
            exclusion_reason="maintenance",
            excluded_at=now,
        )
    with pytest.raises(ValidationError):
        ExcludedRepositoryCatalogEntry(scope=FleetScope.EXCLUDED, repository=repository)


def test_schedule_definition_shape_cannot_reintroduce_generic_trigger_objects() -> None:
    request = schedule_create(interval_minutes=60)
    payload = request.model_dump(mode="json")
    assert payload["interval_minutes"] == 60
    assert "trigger" not in payload
    assert "steps" not in payload

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(request).model_validate(
            {**payload, "trigger": {"kind": "interval", "interval_minutes": 60}}
        )

    with pytest.raises(ValidationError):
        type(request).model_validate({**payload, "interval_minutes": 0})

