"""M165 opt-in durable signer trust-epoch authority for the causal admission DB.

The same SQLite connection that owns RUN_ADMITTED owns trust revocation and
rotation.  No background cache, implicit bootstrap, key generation or physical
execution is provided. POSIX signer files remain credential custody, while
this row is the *only* transactional admission-revocation authority.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from oh_goon.runtime.database import LocalDatabase

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes, digest

SCHEMA = """CREATE TABLE IF NOT EXISTS lace_signer_trust_epoch (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    trust_epoch INTEGER NOT NULL CHECK (trust_epoch > 0),
    policy_identity TEXT NOT NULL,
    root_identity TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'REVOKED'))
)"""


@dataclass(frozen=True, slots=True)
class SignerTrustGrant:
    """A captured epoch, transported with the six signed claims; not a credential."""

    epoch: int
    policy_identity: str
    root_identity: str


def identity_for_roots(roots: Mapping[str, tuple[str, bytes]]) -> str:
    checked = TrustedIssuerVerifier(roots)._roots
    return digest(
        "oh-goon/issuer-trust-roots/v1",
        canonical_bytes(
            {
                field: {"issuer_id": value[0], "public_key_hex": value[1].hex()}
                for field, value in sorted(checked.items())
            }
        ),
    )


def identity_for_policy(snapshot: tuple[tuple[int, int], tuple[int, ...], bytes]) -> str:
    """Hash the owner-observed canonical policy bytes, never secret key bytes."""
    if (
        not isinstance(snapshot, tuple)
        or len(snapshot) != 3
        or not isinstance(snapshot[0], tuple)
        or not isinstance(snapshot[1], tuple)
        or type(snapshot[2]) is not bytes
        or not snapshot[2]
    ):
        raise TrustProtocolError("valid owned signer policy observation required")
    return digest("oh-goon/issuer-signer-policy/v1", snapshot[2])


class SignerTrustEpochAuthority:
    """One optional table in the *existing* admission SQLite database.

    Provision/rotate/revoke are explicit transactions. External administrative
    writers must use these operations (or equivalent same-database CAS); changing
    filesystem policy alone is NOT a durable SQLite revocation event.
    """

    def __init__(
        self, connection: sqlite3.Connection, *, owner: LocalDatabase | None = None
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TrustProtocolError("actual admission SQLite connection required")
        self.connection = connection
        self._database_owner = owner

    def _require_participant_cut(self, connection: sqlite3.Connection) -> None:
        if connection is not self.connection or not connection.in_transaction:
            raise TrustProtocolError("active original signer epoch transaction required")
        if self._database_owner is not None:
            self._database_owner.require_atomic_write_cut(connection)

    def initialize_schema(self) -> None:
        if self.connection.in_transaction:
            raise TrustProtocolError("initialize signer epoch before active admission transactions")
        self.connection.execute(SCHEMA)

    def _transaction(self, action):
        """DB1B2: no standalone administrative SQLite writer in runtime."""
        raise TrustProtocolError("standalone signer transaction forbidden; use LocalDatabase owner")

    def enroll_in(
        self,
        connection: sqlite3.Connection,
        *,
        policy_snapshot,
        roots: Mapping[str, tuple[str, bytes]],
    ) -> SignerTrustGrant:
        """Domain participant; never starts or ends the caller's transaction."""
        self._require_participant_cut(connection)
        policy_id = identity_for_policy(policy_snapshot)
        root_id = identity_for_roots(roots)
        try:
            connection.execute(
                """INSERT INTO lace_signer_trust_epoch
                VALUES (1, 1, ?, ?, 'ACTIVE')""",
                (policy_id, root_id),
            )
        except sqlite3.IntegrityError as exc:
            raise TrustProtocolError("signer trust epoch already provisioned") from exc
        return SignerTrustGrant(1, policy_id, root_id)

    def enroll(
        self, *, policy_snapshot, roots: Mapping[str, tuple[str, bytes]]
    ) -> SignerTrustGrant:
        # Explicit pre-live compatibility; new operational code must use
        # LocalDatabase.enroll_signer_trust_atomically instead.
        return self._transaction(
            lambda: self.enroll_in(self.connection, policy_snapshot=policy_snapshot, roots=roots)
        )

    def active(self) -> SignerTrustGrant:
        row = self.connection.execute("""SELECT trust_epoch, policy_identity, root_identity, state
            FROM lace_signer_trust_epoch WHERE singleton=1""").fetchone()
        if row is None or row[3] != "ACTIVE":
            raise TrustProtocolError("no active durable signer trust epoch")
        return SignerTrustGrant(row[0], row[1], row[2])

    def require_in(
        self,
        connection: sqlite3.Connection,
        *,
        grant: SignerTrustGrant,
        policy_snapshot,
        roots: Mapping[str, tuple[str, bytes]],
    ) -> None:
        if connection is not self.connection or not connection.in_transaction:
            raise TrustProtocolError("signer trust must be fenced on the admission transaction")
        if not isinstance(grant, SignerTrustGrant):
            raise TrustProtocolError("captured signer trust epoch required")
        if grant.policy_identity != identity_for_policy(
            policy_snapshot
        ) or grant.root_identity != identity_for_roots(roots):
            raise TrustProtocolError(
                "signer policy or public trust roots differ from captured epoch"
            )
        row = connection.execute("""SELECT trust_epoch, policy_identity, root_identity, state
            FROM lace_signer_trust_epoch WHERE singleton=1""").fetchone()
        # The operational Journal requires sqlite3.Row on this SAME connection.
        # Compare values, not row-container type (tuple vs sqlite3.Row).
        if row is None or tuple(row) != (
            grant.epoch,
            grant.policy_identity,
            grant.root_identity,
            "ACTIVE",
        ):
            raise TrustProtocolError("signer epoch revoked or superseded before admission commit")

    def revoke_in(self, connection: sqlite3.Connection, *, expected: SignerTrustGrant) -> None:
        """CAS against the original transaction snapshot; no separate COMMIT."""
        self._require_participant_cut(connection)
        if type(expected) is not SignerTrustGrant:
            raise TrustProtocolError("captured signer trust epoch required for revocation")
        result = connection.execute(
            """UPDATE lace_signer_trust_epoch
            SET trust_epoch=trust_epoch+1, state='REVOKED' WHERE singleton=1
            AND trust_epoch=? AND policy_identity=? AND root_identity=? AND state='ACTIVE'""",
            (expected.epoch, expected.policy_identity, expected.root_identity),
        )
        if result.rowcount != 1:
            raise TrustProtocolError("stale signer epoch cannot be revoked")

    def revoke(self, *, expected: SignerTrustGrant) -> None:
        if type(expected) is not SignerTrustGrant:
            raise TrustProtocolError("captured signer trust epoch required for revocation")
        self._transaction(lambda: self.revoke_in(self.connection, expected=expected))

    def rotate_in(
        self,
        connection: sqlite3.Connection,
        *,
        expected: SignerTrustGrant,
        policy_snapshot,
        roots: Mapping[str, tuple[str, bytes]],
    ) -> SignerTrustGrant:
        self._require_participant_cut(connection)
        if type(expected) is not SignerTrustGrant:
            raise TrustProtocolError("captured signer trust epoch required for rotation")
        policy_id = identity_for_policy(policy_snapshot)
        root_id = identity_for_roots(roots)
        if policy_id == expected.policy_identity or root_id == expected.root_identity:
            raise TrustProtocolError(
                "rotation requires new explicit policy and independent public roots"
            )
        result = connection.execute(
            """UPDATE lace_signer_trust_epoch
            SET trust_epoch=trust_epoch+1, policy_identity=?, root_identity=?, state='ACTIVE'
            WHERE singleton=1 AND trust_epoch=? AND policy_identity=? AND root_identity=?
            AND state='ACTIVE'""",
            (policy_id, root_id, expected.epoch, expected.policy_identity, expected.root_identity),
        )
        if result.rowcount != 1:
            raise TrustProtocolError("stale or revoked signer epoch cannot authorize rotation")
        return SignerTrustGrant(expected.epoch + 1, policy_id, root_id)

    def rotate(
        self, *, expected: SignerTrustGrant, policy_snapshot, roots: Mapping[str, tuple[str, bytes]]
    ) -> SignerTrustGrant:
        if type(expected) is not SignerTrustGrant:
            raise TrustProtocolError("captured signer trust epoch required for rotation")
        return self._transaction(
            lambda: self.rotate_in(
                self.connection, expected=expected, policy_snapshot=policy_snapshot, roots=roots
            )
        )
