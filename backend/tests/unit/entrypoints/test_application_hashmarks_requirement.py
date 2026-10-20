from __future__ import annotations

import sys
from typing import NoReturn

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.integrations.hashmarks.runtime_contract import HashmarksRuntimeContractError
from oh_goon.services.effect_recovery import RepositoryEffectRecovery


@pytest.mark.parametrize("standard_local", [True, False])
@pytest.mark.parametrize("failure", [ImportError, OSError, ValueError])
def test_unusable_hashmarks_rejects_before_database_or_effect_recovery(
    monkeypatch: pytest.MonkeyPatch, standard_local: bool, failure: type[Exception]
) -> None:
    import hashmarks

    import oh_goon._application_foundation as foundation

    def unavailable() -> str:
        raise failure("unusable producer package")

    def forbidden_effect(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("startup opened state or recovered effects without required Hashmarks")

    monkeypatch.setattr(hashmarks, "native_producer_implementation_identity", unavailable)
    monkeypatch.setattr(foundation, "LocalDatabase", forbidden_effect)
    monkeypatch.setattr(RepositoryEffectRecovery, "recover", forbidden_effect)

    with pytest.raises(HashmarksRuntimeContractError, match="Hashmarks is required") as rejected:
        ApplicationRuntime.build(standard_local=standard_local)
    assert isinstance(rejected.value.__cause__, failure)


@pytest.mark.parametrize("standard_local", [True, False])
def test_missing_hashmarks_rejects_before_database(
    monkeypatch: pytest.MonkeyPatch, standard_local: bool
) -> None:
    import oh_goon._application_foundation as foundation

    def forbidden_database(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("startup opened state without the required Hashmarks package")

    monkeypatch.setitem(sys.modules, "hashmarks", None)
    monkeypatch.setattr(foundation, "LocalDatabase", forbidden_database)

    with pytest.raises(HashmarksRuntimeContractError, match="Hashmarks is required"):
        ApplicationRuntime.build(standard_local=standard_local)
