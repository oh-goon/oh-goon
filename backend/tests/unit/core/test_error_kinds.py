from __future__ import annotations

from oh_goon.core import errors
from oh_goon.core.errors import ErrorKind, FleetDomainError


def _all_error_types(base: type[FleetDomainError]) -> set[type[FleetDomainError]]:
    found: set[type[FleetDomainError]] = set()
    pending = list(base.__subclasses__())
    while pending:
        candidate = pending.pop()
        found.add(candidate)
        pending.extend(candidate.__subclasses__())
    return found


def test_every_domain_error_has_stable_code_kind_and_retryability() -> None:
    error_types = _all_error_types(FleetDomainError)
    assert error_types
    for error_type in error_types:
        assert error_type.code and error_type.code != FleetDomainError.code
        assert isinstance(error_type.kind, ErrorKind)
        assert isinstance(error_type.retryable, bool)


def test_error_kind_is_transport_independent() -> None:
    assert errors.RunNotFound.kind is ErrorKind.NOT_FOUND
    assert errors.InvalidRequest.kind is ErrorKind.INVALID_REQUEST
    assert errors.ExecutionBusy.kind is ErrorKind.CONFLICT
    assert errors.JournalCorruption.kind is ErrorKind.INTERNAL
