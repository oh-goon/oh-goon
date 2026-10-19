from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.shared_plans import SharedPlanService
from tests.factories.shipped_templates import GOON_TEMPLATES as SHIPPED_GOONS

ROOT = Path(__file__).resolve().parents[4]
STARTER = ROOT / "templates" / "plans" / "python" / "quality" / "plan.yml"


def _shared_quality() -> str:
    return STARTER.read_text(encoding="utf-8")


def test_shared_plan_run_rejects_repository_instance_map_that_does_not_match_selection(
    tmp_path: Path,
) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    store.write("python-quality", _shared_quality())
    plans = SimpleNamespace(run_reviewed=Mock(), require_plan_ingress_preparation=Mock())
    service = SharedPlanService(
        store, SimpleNamespace(), plans, SimpleNamespace(), SimpleNamespace()
    )

    with pytest.raises(FleetPlanInvalid, match="must exactly match selected Teams"):
        service.run(
            "python-quality",
            repository_ids={"repo-a", "repo-b"},
            repository_instance_ids={"repo-a": "instance-a"},
            expected_plan_id="reviewed-plan",
            expected_execution_digest="sha256:" + "d" * 64,
            expected_plan_revision=store.read("python-quality").sha256,
        )
    plans.run_reviewed.assert_not_called()


def test_shared_plan_preview_rejects_comment_only_change_during_real_resolution(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", _shared_quality())
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    (repository_path / ".git").mkdir()
    repository = RepositoryIdentity(path=repository_path)
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)

    class MutatingPlans:
        def resolve_with_source(self, path, **kwargs):
            store.write(
                "python-quality",
                original.content + "\n# concurrent source-only edit\n",
                expected_revision=original.sha256,
            )
            return resolver.resolve_with_source(path, **kwargs)

        def preview_resolved(self, plan, path):  # pragma: no cover - source fence closes first
            raise AssertionError("repository preview must not run from a mixed source cut")

    service = SharedPlanService(
        store,
        SimpleNamespace(repositories=Mock(return_value=(repository,))),
        MutatingPlans(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    with pytest.raises(FleetPlanSourceStale, match="while it was being resolved"):
        service.preview("python-quality", repository_ids={repository.repository_id})


def test_shared_plan_lock_preview_rejects_comment_only_change_during_real_resolution(
    tmp_path: Path,
) -> None:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", _shared_quality())
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)

    class MutatingPlans:
        def resolve_with_source(self, path, **kwargs):
            store.write(
                "python-quality",
                original.content + "\n# concurrent lock-preview edit\n",
                expected_revision=original.sha256,
            )
            return resolver.resolve_with_source(path, **kwargs)

    service = SharedPlanService(
        store, SimpleNamespace(), MutatingPlans(), SimpleNamespace(), SimpleNamespace()
    )

    with pytest.raises(FleetPlanSourceStale, match="while it was being resolved"):
        service.lock_preview("python-quality")


def test_shared_plan_input_projection_rejects_source_change_during_projection(
    tmp_path: Path,
) -> None:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", _shared_quality())
    resolver = FleetPlanResolver(goon_templates=SHIPPED_GOONS)

    class MutatingPlans:
        def input_definitions_with_source(self, path, **kwargs):
            store.write(
                "python-quality",
                original.content + "\n# concurrent input-projection edit\n",
                expected_revision=original.sha256,
            )
            return resolver.input_definitions_with_source(path, **kwargs)

    service = SharedPlanService(
        store, SimpleNamespace(), MutatingPlans(), SimpleNamespace(), SimpleNamespace()
    )

    with pytest.raises(FleetPlanSourceStale, match="while it was being resolved"):
        service.read("python-quality", include_inputs=True)


def test_shared_plan_preview_rejects_repository_replacement_during_preview(tmp_path: Path) -> None:
    from oh_goon.core.models.fleet_plan_evidence import FleetPlanPreview
    from oh_goon.core.models.repository import RepositoryIdentity

    store = SharedPlanFileStore(tmp_path / "plans")
    document = store.write("python-quality", _shared_quality())
    repository_path = tmp_path / "team"
    repository_path.mkdir()
    (repository_path / ".git").mkdir()
    repository = RepositoryIdentity(path=repository_path)
    resolved = SimpleNamespace(plan_id="plan-stable")

    def replace_repository(_plan, path):
        displaced = tmp_path / "team-old"
        path.rename(displaced)
        path.mkdir()
        (path / ".git").mkdir()
        return FleetPlanPreview(plan_id="plan-stable", repository=path, steps=())

    plans = SimpleNamespace(
        resolve_with_source=Mock(return_value=(resolved, document.sha256)),
        preview_resolved=Mock(side_effect=replace_repository),
    )
    service = SharedPlanService(
        store,
        SimpleNamespace(repositories=Mock(return_value=(repository,))),
        plans,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    with pytest.raises(FleetPlanSourceStale, match="changed while it was being previewed"):
        service.preview("python-quality", repository_ids={repository.repository_id})
