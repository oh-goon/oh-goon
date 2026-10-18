from pathlib import Path

from tests.integration.api.client import authenticated_api_client

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from tests.factories.capabilities import capability_snapshot
from tests.factories.repository_state import baseline_for


def test_recovery_status_and_reconciliation_api(tmp_path: Path) -> None:
    dependencies.application()
    database = LocalDatabase()
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempts.start_effect_in(connection, attempt.operation_id)
        attempts.mark_unknown_in(connection, attempt.operation_id, note="process ended")

    with authenticated_api_client(api_app) as client:
        before = client.get("/api/recovery")
        current = before.json()["effect_quarantines"][0]
        reconciled = client.post(
            f"/api/recovery/effects/{attempt.operation_id}/reconcile",
            json={"expected_record_version": current["record_version"]},
        )
        after = client.get("/api/recovery")

    assert before.status_code == 200
    assert before.json()["effect_quarantines"]
    assert reconciled.status_code == 200
    assert reconciled.json()["effect_quarantines"] == []
    assert after.status_code == 200
    assert after.json()["effect_quarantines"] == []


def test_recovery_acknowledgement_requires_reconciliation(tmp_path: Path) -> None:
    dependencies.application()
    database = LocalDatabase()
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempts.start_effect_in(connection, attempt.operation_id)
        attempts.mark_unknown_in(connection, attempt.operation_id, note="process ended")
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")

    with authenticated_api_client(api_app) as client:
        before = client.get("/api/recovery")
        premature = client.post(
            f"/api/recovery/effects/{attempt.operation_id}/acknowledge",
            json={"note": "too early", "expected_record_version": 2},
        )
        current = before.json()["effect_quarantines"][0]
        reconciled = client.post(
            f"/api/recovery/effects/{attempt.operation_id}/reconcile",
            json={"expected_record_version": current["record_version"]},
        )
        acknowledged = client.post(
            f"/api/recovery/effects/{attempt.operation_id}/acknowledge",
            json={
                "note": "inspected",
                "expected_record_version": reconciled.json()["effect_quarantines"][0]["record_version"],
            },
        )

    assert premature.status_code == 409
    assert reconciled.json()["effect_quarantines"][0]["status"] == "PARTIALLY_APPLIED"
    assert acknowledged.status_code == 200
    assert acknowledged.json()["effect_quarantines"] == []
