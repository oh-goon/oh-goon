"""Pre-live SQLite transaction fixtures. NEVER imported by operational source.

The historical opt-in journal/signer tests intentionally exercise the old
standalone transaction protocol on disposable SQLite connections. Keeping their
transaction shim under tests, instead of runtime, prevents it from becoming a
second production owner. Real operational callers use LocalDatabase.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MethodType
from typing import Any

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal as _CausalRunJournal
from oh_goon.runtime.enforcer_lace_journal import _event_identity as _event_identity
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    SignerTrustEpochAuthority as _SignerTrustEpochAuthority,
)
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    SignerTrustGrant as SignerTrustGrant,
)
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    identity_for_policy as identity_for_policy,
)
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    identity_for_roots as identity_for_roots,
)


def _disposable_transaction(connection, action: Callable[[], Any], error: type[Exception]) -> Any:
    if connection.in_transaction:
        raise error(
            "signer epoch mutation cannot nest in Run admission"
            if error is TrustProtocolError
            else "caller has outstanding transaction"
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = action()
        connection.execute("COMMIT")
        return result
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def CausalRunJournal(*args, **kwargs) -> _CausalRunJournal:
    """Return the EXACT real runtime type; only this instance gets test transactions.

    Strict Fleet signer binding deliberately checks type(journal) is the real
    CausalRunJournal. A subclass would invalidate that security invariant.
    """
    journal = _CausalRunJournal(*args, **kwargs)

    def transact(self, action):
        return _disposable_transaction(self.connection, action, CausalJournalError)

    journal._transaction = MethodType(transact, journal)
    return journal


def SignerTrustEpochAuthority(*args, **kwargs) -> _SignerTrustEpochAuthority:
    """Exact real signer authority type, with isolated fixture mutation only."""
    authority = _SignerTrustEpochAuthority(*args, **kwargs)

    def transact(self, action):
        return _disposable_transaction(self.connection, action, TrustProtocolError)

    authority._transaction = MethodType(transact, authority)
    return authority
