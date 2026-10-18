"""CO-R3O: original CORE-native identity and receipt fail-closed guard."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _R3_QUALIFIER,
    _R3N_TEST,
    check_co_r01_lifecycle_edges,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def test_original_native_core_extension_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(("relative", "before", "after"), [
    (_R3_QUALIFIER, "--require-module test_co_r3n_native_cgroup_v2_witness", "--require-module test_fake_cgroup"),
    (_R3_QUALIFIER, "if (( last_result == 0 )) && [[ -s \"$evidence/CORE.json\" ]]; then", "if true; then"),
    (_R3_QUALIFIER, "status=REQUESTED_UNQUALIFIED", "status=PASS"),
    (_R3_QUALIFIER, "status=FAILED_OR_INCOMPLETE", "status=PASS"),
    (_R3N_TEST, "assert result.returncode != 0", "assert result.returncode == 0"),
    (_R3N_TEST, 'assert "CO_R3O_PAYLOAD_EXECUTED" not in result.stdout', 'assert "CO_R3O_PAYLOAD_EXECUTED" in result.stdout'),
    (_R3N_TEST, 'assert {"cpu", "memory", "pids"} <= actual', 'assert True'),
    (_R3N_TEST, 'lease.nested_identity = None', 'pass # missing identity no longer checked'),
])
def test_native_core_bypass_mutation_rejected(
    tmp_path: Path, relative: str, before: str, after: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    original = target.read_text(encoding="utf-8")
    assert before in original
    target.write_text(original.replace(before, after, 1), encoding="utf-8")
    assert any(v.path == target for v in check_co_r01_lifecycle_edges(root))


def test_hosted_native_preflight_does_not_touch_non_cgroupfs(tmp_path: Path) -> None:
    import runpy

    witness = runpy.run_path(str(ROOT / _R3N_TEST), run_name="co_r3o_native_preflight")
    root = tmp_path / "oh-goon-native-candidate"
    root.mkdir()
    with pytest.raises(RuntimeError, match="cgroup-v2"):
        witness["_require_disposable_delegated_cgroup"](
            root, "24 1 0:2 / / rw - tmpfs tmpfs rw",
        )
    assert list(root.iterdir()) == []
