from __future__ import annotations

"""Fleet snapshot provenance derived from canonical projection source facts."""

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.provenance import FleetSnapshotProvenance
from oh_goon.core.projections.fleet_context import FleetProjectionContext
from oh_goon.core.time import utc_now


def snapshot_payload(
    context: FleetProjectionContext,
    *,
    durable_revision: int,
    journal_revision: int,
) -> dict[str, object]:
    """Canonical source facts for one Fleet snapshot identity.

    Summary, attention, review preparation, and lineage manifests are derived views of
    these facts and therefore do not participate as independent snapshot inputs.
    """
    return {
        "durable_revision": durable_revision,
        "journal_revision": journal_revision,
        "repositories": [repo.model_dump(mode="json", exclude_computed_fields=True) for repo in context.repositories],
        "active_fleet_run": (
            context.active_fleet_run.model_dump(mode="json", exclude_computed_fields=True)
            if context.active_fleet_run
            else None
        ),
        "latest_fleet_runs": [
            detail.model_dump(mode="json", exclude_computed_fields=True)
            for detail in context.latest_fleet_runs
        ],
    }


def build_snapshot_provenance(
    context: FleetProjectionContext,
    *,
    durable_revision: int,
    journal_revision: int,
) -> FleetSnapshotProvenance:
    payload = snapshot_payload(
        context,
        durable_revision=durable_revision,
        journal_revision=journal_revision,
    )
    return FleetSnapshotProvenance(
        snapshot_id=stable_id("snapshot", payload),
        generated_at=utc_now(),
        durable_revision=durable_revision,
        journal_revision=journal_revision,
    )
