"""CO-R3N original QENV1 native witness enrollment and fail-closed preflight."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _R3_QUALIFIER,
    _R3N_TEST,
    check_co_r01_lifecycle_edges,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def test_co_r3n_original_qualifier_contract_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (
            _R3_QUALIFIER,
            "tests/native/test_co_r3n_native_cgroup_v2_witness.py",
            "tests/native/witness_skipped.py",
        ),
        (_R3_QUALIFIER, "R3N_NATIVE_CGROUP_STATUS.txt", "R3N_NATIVE_MISSING.txt"),
        (
            _R3_QUALIFIER,
            'if [[ -n "${QUALIFY_R3N_CGROUP_ROOT:-}" ]]; then',
            'if [[ -z "${QUALIFY_R3N_CGROUP_ROOT:-}" ]]; then',
        ),
        (_R3N_TEST, "assert result.returncode == 77", "assert result.returncode != 77"),
        (
            _R3N_TEST,
            "cg._reconcile_stale_cgroups(lease.root, lease.state_file.parent)",
            "pass # bypass recovery",
        ),
        (_R3N_TEST, "lease.close()", "pass # bypass original teardown"),
    ],
)
def test_co_r3n_native_witness_bypass_rejected(
    tmp_path: Path, relative: str, before: str, after: str
) -> None:
    root = _fixture(tmp_path)
    path = root / relative
    content = path.read_text(encoding="utf-8")
    assert before in content
    path.write_text(content.replace(before, after, 1), encoding="utf-8")
    assert any(v.path == path for v in check_co_r01_lifecycle_edges(root))


def test_non_cgroup_filesystem_is_rejected_without_effects(tmp_path: Path) -> None:
    import runpy

    module = runpy.run_path(str(ROOT / _R3N_TEST), run_name="co_r3n_native_preflight")
    root = tmp_path / "oh-goon-native-test"
    root.mkdir()
    with pytest.raises(RuntimeError, match="cgroup-v2"):
        module["_require_disposable_delegated_cgroup"](
            root, "24 1 0:2 / / rw - tmpfs tmpfs rw"
        )
    assert list(root.iterdir()) == []


def test_native_unset_root_rejected_before_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    module = runpy.run_path(str(ROOT / _R3N_TEST), run_name="co_r3n_native_preflight")
    monkeypatch.delenv("QUALIFY_R3N_CGROUP_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="explicit QUALIFY_R3N_CGROUP_ROOT"):
        module["_native_parent"]()
