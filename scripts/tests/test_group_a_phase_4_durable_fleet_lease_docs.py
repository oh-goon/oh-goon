"""Keep M153 lease ownership and rollout limits visible across code/docs."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_only_existing_sqlite_lease_owner_is_used_by_enforcer() -> None:
    owner = (ROOT / "backend/src/oh_goon/runtime/execution/sqlite_lease.py").read_text()
    enforcer = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    enforcer = ast.unparse(ast.parse(enforcer))
    assert "def require_initial_fleet_control_in(" in owner
    assert "def initial_fleet_control_is_current(" in owner
    assert "def publish_with_owned_fleet_control(" in enforcer
    assert "lease_is_current=lease_store.initial_fleet_control_is_current" in enforcer
    assert "store.require_initial_fleet_control_in(" in enforcer
    assert "control_epoch=1, lease_identity=lease.token" in enforcer
    assert "Path(main).samefile(self._database.path)" in owner
    assert "row[6] is not None" in owner
    assert "self._environment.process_identity(pid) != birth" in owner
