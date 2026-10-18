from __future__ import annotations

from pathlib import Path


def test_certification_bootstrap_tests_do_not_reacquire_deleted_private_materializer() -> None:
    project_root = Path(__file__).resolve().parents[3]
    source = (project_root / "scripts/tests/test_certification_bootstrap.py").read_text(
        encoding="utf-8"
    )

    assert "_materialize_local_executor_inventory" not in source
