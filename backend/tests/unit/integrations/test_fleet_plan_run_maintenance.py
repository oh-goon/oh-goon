from types import SimpleNamespace

from oh_goon.integrations.fleet_plan_run import PreparedPlanRun


class _Temporary:
    def __init__(self) -> None:
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        self.cleanup_calls += 1


class _Store:
    def __init__(self) -> None:
        self.calls = 0
        self.receipt = object()

    def maintain_retained_content(self):
        self.calls += 1
        return self.receipt


def test_close_runs_bounded_content_maintenance_once_per_prepared_run() -> None:
    run = object.__new__(PreparedPlanRun)
    store = _Store()
    temporary = _Temporary()
    run._lineage = SimpleNamespace(content_store=store)
    run._maintenance_receipt = None
    run._temporary = temporary

    run.close()
    run.close()

    assert store.calls == 1
    assert run.maintenance_receipt is store.receipt
    assert temporary.cleanup_calls == 2
