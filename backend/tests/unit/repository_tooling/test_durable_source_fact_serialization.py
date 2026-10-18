from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "src" / "oh_goon"


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_workspace_quiescence_persistence_excludes_computed_fields() -> None:
    source = _source("runtime/workspace_leases.py")
    assert 'quiescence.model_dump(mode="json", exclude_computed_fields=True)' in source


def test_dependency_source_provenance_excludes_computed_fields() -> None:
    source = _source("runtime/_dependency_observation_storage.py")
    assert (
        'item.model_dump(mode="json", exclude_computed_fields=True) for item in snapshot.sources'
        in source
    )


def test_execution_lock_persistence_excludes_computed_fields() -> None:
    source = _source("services/execution_lock_persistence.py")
    assert (
        'item.model_dump(mode="json", exclude_computed_fields=True) for item in lock.execution'
        in source
    )
