"""M177: the existing Fleet replay owner fails before planning or native observation.

This is deliberately a rejection cut, not a replay implementation. The public
rerun/readmission API is retained, but no Fleet Plan lineage can be admitted by
production composition until a separate native authenticated cutover owns it.
"""
from __future__ import annotations

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.vocabulary import OperationAction
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_policy_only_fleet_provisioning import policy_only
from tests.unit.services.test_group_a_strict_cicd_ingress import strict_fixture


@pytest.mark.parametrize("mode", ["direct", "strict"])
@pytest.mark.parametrize("lineage", ["rerun_from_run_id", "readmitted_from_run_id"])
def test_enrolled_fleet_replay_rejected_before_planning_or_native_observation(
    tmp_path, monkeypatch, mode, lineage,
):
    if mode == "strict":
        args, connection, lifecycle, pool, _, _admit, _observed, issuer_calls = strict_fixture(
            tmp_path, monkeypatch
        )
    else:
        args, connection, issuer_calls, lifecycle, _, _admit, _observed = policy_only(
            tmp_path, monkeypatch
        )
        pool = None

    class NeverPlanner:
        def plan(self, *args, **kwargs):
            pytest.fail("replay must not reach repository/Plan planning")

    class NeverWorker:
        def submit(self, *args, **kwargs):
            pytest.fail("replay must not schedule physical work")

    control = FleetRunControlPlane(
        NeverPlanner(), args["journal"], lifecycle, object(), NeverWorker(), object()
    )
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target)

    def never_resolve(self, run_id):
        pytest.fail("replay must not observe issuer keys, signer epoch or executor inventory")

    monkeypatch.setattr(FleetPreworkProvisioner, "resolve", never_resolve)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(args["journal"], "read_view", lambda: pytest.fail(
                "replay must not enter idempotent journal lookup before the lineage cutover"
            ))
            with pytest.raises(TrustProtocolError, match="replay Fleet ingress requires"):
                control._start_intent(
                    intent, repository_scope=args["repository_scope"],
                    fleet_run_id="durable-run", source=args["intent"].source,
                    readmitted_from_run_id="earlier-run" if lineage == "readmitted_from_run_id" else None,
                    rerun_from_run_id="earlier-run" if lineage == "rerun_from_run_id" else None,
                )
        assert facts(args, connection) == (None, 0, 0)
        assert connection.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM executor_capacity_reservations").fetchone()[0] == 0
        if pool is not None:
            assert pool.placement_queue().entries == ()
        assert issuer_calls == []
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["direct", "strict"])
def test_normal_enrolled_fleet_ingress_still_reaches_existing_planner(tmp_path, monkeypatch, mode):
    if mode == "strict":
        args, connection, lifecycle, _pool, _, _admit, _observed, _calls = strict_fixture(
            tmp_path, monkeypatch
        )
    else:
        args, connection, _calls, lifecycle, _, _admit, _observed = policy_only(
            tmp_path, monkeypatch
        )

    class PlannerReached(Exception):
        pass

    class Planner:
        def plan(self, *args, **kwargs):
            raise PlannerReached

    control = FleetRunControlPlane(Planner(), args["journal"], lifecycle,
                                   object(), object(), object())
    try:
        with pytest.raises(PlannerReached):
            control._start_intent(
                resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target),
                repository_scope=args["repository_scope"], fleet_run_id="durable-run",
                source=args["intent"].source, readmitted_from_run_id=None,
                rerun_from_run_id=None,
            )
        assert facts(args, connection) == (None, 0, 0)
    finally:
        connection.close()
