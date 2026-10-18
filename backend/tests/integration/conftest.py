from pathlib import Path

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app


def clear_dependency_caches() -> None:
    runtime = dependencies.application.cache_info().currsize
    if runtime:
        try:
            dependencies.application().shutdown(wait_for_runs=False)
        except Exception:
            pass
    dependencies.application.cache_clear()


@pytest.fixture(autouse=True)
def isolated_local_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "oh-goon.db"))
    clear_dependency_caches()
    api_app.dependency_overrides.clear()
    yield
    api_app.dependency_overrides.clear()
    clear_dependency_caches()
