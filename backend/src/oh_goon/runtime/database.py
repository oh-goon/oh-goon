from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, local
from typing import TYPE_CHECKING, Literal, Mapping, TypeVar, cast

if TYPE_CHECKING:
    from oh_goon.core.enforcer_lace_trust import (
        AdmittedRunContract,
        AuthenticatedReceipt,
        ReceiptAuthenticator,
    )
    from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustGrant
from uuid import uuid4

from oh_goon.core.errors import DatabaseSchemaMismatch
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_DELETE as SQLITE_JOURNAL_DELETE
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_WAL as SQLITE_JOURNAL_WAL
from oh_goon.runtime._database_policy import (
    DATABASE_SCHEMA_POLICY,
    sqlite_journal_mode_for_path,
)
from oh_goon.runtime._database_policy import (
    DATABASE_SCHEMA_VERSION as DATABASE_SCHEMA_VERSION,
)
from oh_goon.runtime._database_policy import (
    database_filesystem_is_local as database_filesystem_is_local,
)
from oh_goon.runtime._database_policy import (
    database_filesystem_type as database_filesystem_type,
)
from oh_goon.runtime._database_policy import (
    database_path_for_environment as database_path_for_environment,
)
from oh_goon.runtime._database_policy import (
    default_database_path as default_database_path,
)
from oh_goon.runtime._database_policy import (
    inspect_database_path as inspect_database_path,
)
from oh_goon.runtime._database_policy import inspect_local_database as inspect_local_database
from oh_goon.runtime._database_schema import _SCHEMA_SQL
from oh_goon.runtime._sqlite_access_diagnostics import SQLiteAccessDiagnostics
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate, sqlite_authority_gate_path
from oh_goon.runtime._sqlite_connection_configuration import configure_common_connection
from oh_goon.runtime._sqlite_identity_error import sqlite_identity_error as _sqlite_identity_error
from oh_goon.runtime._sqlite_observer_authority import admitted_observer_authorizer
from oh_goon.runtime.durable_revision import (
    advance_durable_revision,
    ensure_durable_revision_schema,
    read_durable_revision,
)
from oh_goon.runtime.exclusions import ensure_exclusion_schema
from oh_goon.runtime.local_state_maintenance import (
    LocalStateCompatibility,
    mismatch_message,
)
from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent
from oh_goon.runtime.schema_version_policy import (
    DurableSchemaDisposition,
)

ADMITTED_SQLITE_REPROOF_ATTEMPTS = 3
ADMITTED_SQLITE_REPROOF_DELAYS_SECONDS = (0.25, 1.0)
DATABASE_IDENTITY_COOKIE_MAX = 0x7FFFFFFF

T = TypeVar("T")


def _require_private_backup_directory(path: Path) -> Path:
    """Never chmod an arbitrary existing directory to make a backup private."""

    if path.resolve() != path:
        raise RuntimeError("backup directory cannot use a symlink or noncanonical path")
    if not path.exists():
        ensure_private_directory(path)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("backup destination parent must be a directory")
    if os.name == "posix" and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
        raise PermissionError("backup directory must be owner-owned and private")
    return path


def _new_database_identity_cookie() -> int:
    # SQLite application_id is a persisted signed 32-bit header field. Oh-Goon
    # uses a non-zero positive cookie as per-database instance identity; user_version
    # remains the schema epoch. Existing pre-cookie databases are assigned one once
    # during normal LocalDatabase initialization.
    return secrets.randbelow(DATABASE_IDENTITY_COOKIE_MAX) + 1


@dataclass(frozen=True, slots=True)
class DatabasePathIdentity:
    device: int
    inode: int
    size: int
    filesystem: str | None


@dataclass(frozen=True, slots=True)
class LocalStateBackupReceipt:
    """Offline owner-observed snapshot; NOT a signer or runtime admission receipt."""

    path: Path
    sha256: str
    durable_revision: int
    database_identity_cookie: int


class DatabasePathIdentityLost(sqlite3.DatabaseError):
    """The configured database path stopped naming the admitted SQLite file."""


class BackupPublicationUncertain(sqlite3.DatabaseError):
    """Backup publication or removal cannot be proven durable; operator review required."""


def _sync_backup_directory(directory: Path) -> None:
    """Durability step owned by the one offline backup operation."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(directory, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class CausalAdmissionCommit:
    """Typed DB1B2 pre-live result; NEVER a live execution permission.

    ALREADY_COMMITTED is returned only after authenticated durable reread of the
    exact contract, epoch, lease, receipt and caller-proved operational fact.
    """

    disposition: Literal["COMMITTED", "ALREADY_COMMITTED"]
    run_id: str
    contract_identity: str
    control_epoch: int
    lease_identity: str


def _capture_database_path_identity(path: Path) -> DatabasePathIdentity:
    """Observe only filesystem identity for the admitted database path.

    SQLite owns interpretation of SQLite bytes.  Re-reading the first 16 bytes of an
    actively used database through a second raw file handle duplicates that authority
    and can misclassify transient host-filesystem observations as replacement/corruption.
    Path identity is therefore dev/inode based; SQLite schema identity is proven through
    the SQLite connection itself at every owner boundary.
    """

    try:
        if path.is_symlink() or not path.is_file():
            raise DatabasePathIdentityLost(
                f"database path identity is invalid: expected regular non-symlink file at {path}"
            )
        stat_result = path.stat()
    except OSError as exc:
        raise DatabasePathIdentityLost(
            f"database path identity cannot be observed at {path}: {exc}"
        ) from exc
    return DatabasePathIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        filesystem=database_filesystem_type(path),
    )


def _database_path_observation(path: Path, admitted: DatabasePathIdentity | None) -> str:
    try:
        current = _capture_database_path_identity(path)
    except DatabasePathIdentityLost as exc:
        return f"admitted={admitted!r}, current=unavailable({exc})"
    return f"admitted={admitted!r}, current={current!r}"


class LocalDatabase:
    """One SQLite database and runtime-session identity for all durable local facts."""

    def __init__(self, path: Path | None = None, *, journal_mode: str | None = None) -> None:
        self.path = (path or default_database_path()).expanduser().resolve()
        self.session_id = f"session-{uuid4().hex}"
        self._path_identity: DatabasePathIdentity | None = None
        self._schema_cookie: int | None = None
        self._database_identity_cookie: int | None = None
        self._durable_revision_floor = 0
        self._durable_revision_lock = RLock()
        self._connection_scope = local()
        self._recovery_observer: Callable[[ExecutionRecoveryEvent], None] | None = None
        self.sqlite_diagnostics = SQLiteAccessDiagnostics()
        # Resolve filesystem and journal authority under the SAME admission gate
        # used to initialize the original SQLite owner. Never recompute AUTO
        # after admission or switch engines/modes behind the runtime.
        with sqlite_authority_gate(self.path, write=True):
            self.journal_mode = sqlite_journal_mode_for_path(self.path, requested=journal_mode)
            self._initialize()
            self._path_identity = _capture_database_path_identity(self.path)

    @staticmethod
    @contextmanager
    def observe_existing_for_reset(path: Path) -> Iterator[sqlite3.Connection]:
        """Provide native read-only maintenance observation without runtime admission.

        The canonical gate and read transaction remain held until the reset owner
        has finished its explicit file deletion. This never initializes schema,
        changes journal mode, repairs evidence, or admits an execution.
        """
        with sqlite_authority_gate(path, write=True):
            if path.is_symlink() or not path.is_file():
                raise sqlite3.DatabaseError(
                    "Reset observation requires an existing regular database"
                )
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
            try:
                configure_common_connection(path, connection)
                connection.execute("PRAGMA query_only = ON")
                connection.execute("BEGIN")
                yield connection
            finally:
                connection.close()

    @property
    def private_paths(self) -> tuple[Path, ...]:
        """Exact active SQLite state, including native transient sidecars and its gate."""
        return (
            self.path,
            *(
                self.path.with_name(self.path.name + suffix)
                for suffix in ("-wal", "-shm", "-journal")
            ),
            sqlite_authority_gate_path(self.path),
        )

    def export_verified_backup(self, destination: Path) -> LocalStateBackupReceipt:
        """Explicit offline SQLite snapshot through the ORIGINAL database owner.

        SQLite's VACUUM INTO writes a consistent standalone backup without
        copying a live WAL/journal or introducing a second operational DB owner.
        The target is published using an exclusive hardlink after verification
        and fsync, so an existing backup cannot be overwritten. Never restores
        or switches the current admitted database path automatically.
        """
        requested = Path(destination).expanduser().absolute()
        if requested.name in {"", ".", ".."}:
            raise ValueError("explicit backup filename required")
        if getattr(self._connection_scope, "active_cut", None) is not None:
            raise RuntimeError("backup requires an independent explicit maintenance cut")
        with sqlite_authority_gate(self.path, write=True):
            self._require_path_identity()
            connection = self._connect_with_context("explicit offline SQLite backup")
            try:
                self._require_durable_connection_policy(connection)
                connection.execute("BEGIN")
                try:
                    self._prove_admitted_sqlite_identity(connection, context="explicit backup")
                    revision = read_durable_revision(connection)
                    cookie = int(connection.execute("PRAGMA application_id").fetchone()[0])
                finally:
                    connection.rollback()
                # Fail-before-work: no destination directory or temporary
                # file is touched until the original owner has re-proved
                # SQLite identity, durability and the exact revision.
                target_dir = _require_private_backup_directory(requested.parent)
                target = target_dir / requested.name
                if target == self.path or target.is_symlink() or target.exists():
                    raise FileExistsError(
                        f"backup destination already exists or aliases state: {target}"
                    )
                staged = target_dir / f".{target.name}.{uuid4().hex}.snapshot-tmp"
                published_identity: tuple[int, int] | None = None
                receipt: LocalStateBackupReceipt | None = None
                try:
                    connection.execute("VACUUM INTO ?", (str(staged),))
                    # The existing read-only inspector is the canonical SQLite
                    # integrity authority; no parallel validator is introduced.
                    inspection = inspect_database_path(
                        staged, integrity_check=True, foreign_key_check=True
                    )
                    if inspection.status != LocalStateCompatibility.READY:
                        raise sqlite3.DatabaseError(
                            f"exported backup failed SQLite inspection: {inspection.detail}"
                        )
                    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(staged, flags)
                    try:
                        metadata = os.fstat(descriptor)
                        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                            raise RuntimeError("backup must be a single-link regular file")
                        digest = hashlib.sha256()
                        while chunk := os.read(descriptor, 1024 * 1024):
                            digest.update(chunk)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    # DB4: before publication prove the SNAPSHOT itself kept the
                    # exact source cookie and durable revision. SQLite integrity
                    # alone cannot detect a valid but wrong/stale database.
                    self.verify_offline_backup(
                        LocalStateBackupReceipt(
                            path=staged,
                            sha256=digest.hexdigest(),
                            durable_revision=revision,
                            database_identity_cookie=cookie,
                        )
                    )
                    # Atomic no-clobber publish: link() refuses an existing
                    # destination, unlike os.replace() which would overwrite it.
                    os.link(staged, target, follow_symlinks=False)
                    published_identity = (metadata.st_dev, metadata.st_ino)
                    # The temporary link must disappear BEFORE the directory
                    # sync; only then is the final directory state durable.
                    staged.unlink()
                    _sync_backup_directory(target_dir)
                    receipt = LocalStateBackupReceipt(
                        path=target,
                        sha256=digest.hexdigest(),
                        durable_revision=revision,
                        database_identity_cookie=cookie,
                    )
                    # A successful acknowledgement describes the published
                    # name, not just its previously verified staging name.
                    self.verify_offline_backup(receipt)
                except BaseException:
                    if published_identity is not None:
                        try:
                            present = target.lstat()
                            if (present.st_dev, present.st_ino) != published_identity:
                                raise BackupPublicationUncertain(
                                    "DB5 backup target replaced after publication; never delete another owner's file"
                                )
                            target.unlink()
                            _sync_backup_directory(target_dir)
                        except (OSError, BackupPublicationUncertain) as cleanup_error:
                            raise BackupPublicationUncertain(
                                f"DB5 backup publication/removal durability uncertain at {target}; "
                                "inspect offline candidate, never use as runtime authority"
                            ) from cleanup_error
                    raise
                finally:
                    staged.unlink(missing_ok=True)
            finally:
                connection.close()
        assert receipt is not None
        return receipt

    @staticmethod
    def verify_offline_backup(receipt: LocalStateBackupReceipt) -> None:
        """Read-only evidence check; NEVER installs or admits the snapshot.

        A verified offline file is only a recovery *candidate*. Every actual
        restart must still pass normal LocalDatabase admission and independent
        external trust/physical gates. No Scout fallback or automatic restore.
        """
        path = Path(receipt.path).expanduser().absolute()
        if path.parent.resolve() != path.parent or path.is_symlink():
            raise sqlite3.DatabaseError("DB4 offline backup path is not canonical")
        try:
            before = path.lstat()
        except OSError as exc:
            raise sqlite3.DatabaseError("DB4 offline backup missing") from exc
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise sqlite3.DatabaseError("DB4 offline backup must be single-link regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            pinned = os.fstat(descriptor)
            if (pinned.st_dev, pinned.st_ino) != (before.st_dev, before.st_ino):
                raise sqlite3.DatabaseError("DB4 offline backup path changed before read")
            observed_hash = hashlib.sha256()
            while block := os.read(descriptor, 1024 * 1024):
                observed_hash.update(block)
            if observed_hash.hexdigest() != receipt.sha256:
                raise sqlite3.DatabaseError("DB4 offline backup SHA-256 mismatch")
            # Reuse the existing SQLite integrity inspector; only read-only
            # sqlite3 is used below, by the ORIGINAL LocalDatabase owner.
            inspection = inspect_database_path(path, integrity_check=True, foreign_key_check=True)
            if inspection.status != LocalStateCompatibility.READY:
                raise sqlite3.DatabaseError(
                    f"DB4 offline backup integrity failure: {inspection.detail}"
                )
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as reader:
                cookie = int(reader.execute("PRAGMA application_id").fetchone()[0])
                revision = read_durable_revision(reader)
            if (cookie, revision) != (receipt.database_identity_cookie, receipt.durable_revision):
                raise sqlite3.DatabaseError("DB4 offline backup cookie/revision mismatch")
            after = os.fstat(descriptor)
            current = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            ):
                raise sqlite3.DatabaseError("DB4 offline backup changed during verification")
        finally:
            os.close(descriptor)

    def set_recovery_observer(
        self, observer: Callable[[ExecutionRecoveryEvent], None] | None
    ) -> None:
        """Attach passive recovery diagnostics after runtime diagnostics are composed."""

        self._recovery_observer = observer

    def _emit_recovery_event(self, event: ExecutionRecoveryEvent) -> None:
        self.sqlite_diagnostics.record_recovery(
            event, getattr(self._connection_scope, "open_context", None)
        )
        observer = self._recovery_observer
        if observer is None:
            return
        try:
            observer(event)
        except Exception:
            # Diagnostics are representation only and must never alter SQLite authority.
            pass

    @contextmanager
    def execution_connection_scope(self) -> Iterator[None]:
        """Reuse one admitted RW SQLite handle for one execution thread.

        Certification has one process-local worker owner, but an execution can perform many
        short durable cuts before, around and after expensive immutable-input preparation.
        Reopening the same SQLite file for every cut created a second reconnect surface even
        after cancellation observation and foreground wait polling were fixed. This scope
        reuses only the native connection handle; every transaction remains explicit and
        re-proves the admitted path/SQLite identity before BEGIN. Nested scopes reuse the
        same thread-owned handle. Parallel target threads therefore never share a connection.
        """

        existing = getattr(self._connection_scope, "connection", None)
        if existing is not None:
            self._connection_scope.depth += 1
            try:
                yield
            finally:
                self._connection_scope.depth -= 1
            return

        connection = self._connect_with_context("execution connection scope")
        self._connection_scope.connection = connection
        self._connection_scope.depth = 1
        try:
            yield
        finally:
            self._connection_scope.depth = 0
            self._connection_scope.connection = None
            connection.close()

    def _transaction_connection(self) -> tuple[sqlite3.Connection, bool]:
        scoped = getattr(self._connection_scope, "connection", None)
        if scoped is None:
            return self._connect_with_context("runtime transaction"), True
        self._require_path_identity()
        return scoped, False

    def require_atomic_write_cut(self, connection: sqlite3.Connection) -> None:
        """Prove a named domain operation participates in THIS owner's write cut.

        A raw SQLite ``BEGIN IMMEDIATE`` on the same file (or even on a handle
        borrowed from this database) is not an admitted OH-Goon transaction.
        This method does not decide Enforcer admission; it only proves that the
        caller's domain operation is part of the existing identity-checked cut.
        """

        active = getattr(self._connection_scope, "active_cut", None)
        if (
            active is None
            or active[0] is not connection
            or active[1] is not True
            or not connection.in_transaction
        ):
            raise RuntimeError("original LocalDatabase atomic write cut required")

    @contextmanager
    def _transaction_cut(
        self,
        connection: sqlite3.Connection,
        *,
        write: bool,
        context: str,
    ) -> Iterator[sqlite3.Connection]:
        """Bind path/SQLite identity proof to the exact transaction snapshot it certifies."""

        with sqlite_authority_gate(self.path, write=write):
            if getattr(self._connection_scope, "active_cut", None) is not None:
                raise RuntimeError("nested operational database transaction forbidden")
            self._require_path_identity()
            try:
                # The scoped handle is intentionally reused, but cached pager pages must not
                # become authority for a new durable cut. This PRAGMA is itself inside the
                # cross-process gate so it cannot race an Oh-Goon rollback-journal writer.
                connection.execute("PRAGMA shrink_memory")
                # The long-lived execution connection can be changed between
                # cuts by the code holding it. Reasserting FULL only at open
                # would permit a later transaction with synchronous=OFF. Fail
                # before BEGIN/effects instead of silently repairing a bad cut.
                self._require_durable_connection_policy(connection)
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                self._prove_admitted_sqlite_identity(connection, context=context)
                self._connection_scope.active_cut = (connection, write)
                yield connection
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._connection_scope.active_cut = None

    @contextmanager
    def transaction(
        self,
        *,
        write: bool = False,
        track_revision: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        connection, close_when_done = self._transaction_connection()
        context = "runtime transaction" if close_when_done else "scoped runtime transaction"
        self.sqlite_diagnostics.record(context, "transactions")
        committed_revision: int | None = None
        try:
            with self._transaction_cut(
                connection, write=write, context=context
            ) as transaction_connection:
                changes_before = transaction_connection.total_changes
                yield transaction_connection
                if (
                    write
                    and track_revision
                    and transaction_connection.total_changes > changes_before
                ):
                    committed_revision = advance_durable_revision(transaction_connection)
                transaction_connection.commit()
                if committed_revision is not None:
                    self._publish_durable_revision(committed_revision)
        except BaseException as exc:
            if isinstance(exc, sqlite3.DatabaseError):
                self._raise_if_path_identity_lost(exc)
            raise
        finally:
            if close_when_done:
                connection.close()

    @contextmanager
    def _guard_atomic_domain_participant(self, connection: sqlite3.Connection) -> Iterator[None]:
        """Deny participant BEGIN/COMMIT/ROLLBACK on the original SQLite cut.

        Only the canonical owner may finish the cut. This is a local correctness
        fence, not a sandbox against arbitrary trusted Python code replacing
        SQLite authorizers.
        """
        self.require_atomic_write_cut(connection)

        def deny_transaction(
            action: int,
            operation: str | None,
            _arg2: str | None,
            _db: str | None,
            _source: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_TRANSACTION:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_transaction)
        try:
            yield
            self.require_atomic_write_cut(connection)
        finally:
            connection.set_authorizer(None)

    def enroll_signer_trust_atomically(
        self,
        *,
        policy_snapshot: tuple[tuple[int, int], tuple[int, ...], bytes],
        roots: Mapping[str, tuple[str, bytes]],
    ) -> SignerTrustGrant:
        """Canonical SQLite enrollment; signer domain checks stay with signer owner."""
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority

        with self.transaction(write=True) as connection:
            with self._guard_atomic_domain_participant(connection):
                return SignerTrustEpochAuthority(connection, owner=self).enroll_in(
                    connection, policy_snapshot=policy_snapshot, roots=roots
                )

    def revoke_signer_trust_atomically(self, *, expected: SignerTrustGrant) -> None:
        """A stale signer generation may not commit a competing revocation."""
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority

        with self.transaction(write=True) as connection:
            with self._guard_atomic_domain_participant(connection):
                SignerTrustEpochAuthority(connection, owner=self).revoke_in(
                    connection, expected=expected
                )

    def rotate_signer_trust_atomically(
        self,
        *,
        expected: SignerTrustGrant,
        policy_snapshot: tuple[tuple[int, int], tuple[int, ...], bytes],
        roots: Mapping[str, tuple[str, bytes]],
    ) -> SignerTrustGrant:
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority

        with self.transaction(write=True) as connection:
            with self._guard_atomic_domain_participant(connection):
                return SignerTrustEpochAuthority(connection, owner=self).rotate_in(
                    connection, expected=expected, policy_snapshot=policy_snapshot, roots=roots
                )

    def transfer_causal_control_atomically(
        self,
        *,
        run_id: str,
        epoch: int,
        lease_identity: str,
        next_epoch: int,
        next_lease_identity: str,
        signer_grant: SignerTrustGrant,
        policy_snapshot: tuple[tuple[int, int], tuple[int, ...], bytes],
        roots: Mapping[str, tuple[str, bytes]],
    ) -> None:
        """DB1B2 pre-live transfer with an exact signer fence in ONE SQLite cut.

        Not a second live Fleet lease authority or a replacement for Enforcer.
        """
        from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority

        with self.transaction(write=True) as connection:
            with self._guard_atomic_domain_participant(connection):
                SignerTrustEpochAuthority(connection, owner=self).require_in(
                    connection,
                    grant=signer_grant,
                    policy_snapshot=policy_snapshot,
                    roots=roots,
                )
                CausalRunJournal(connection, owner=self).transfer_control_in(
                    connection,
                    run_id,
                    epoch=epoch,
                    lease_identity=lease_identity,
                    next_epoch=next_epoch,
                    next_lease_identity=next_lease_identity,
                )

    def publish_causal_admission_atomically(
        self,
        *,
        contract: AdmittedRunContract,
        receipt: AuthenticatedReceipt,
        authenticator: ReceiptAuthenticator,
        control_epoch: int,
        lease_identity: str,
        operational_admission: Callable[[sqlite3.Connection], T],
        before_commit: Callable[[], None] | None = None,
        operational_committed: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> T | CausalAdmissionCommit:
        """Explicit pre-live named atomic admission, not an alternate Fleet launcher.

        The deployed Fleet path remains FleetAdmissionPlanner under this same
        LocalDatabase write cut. This method only replaces the old opt-in
        CausalRunJournal transaction wrapper for administrative callers.
        """
        from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal

        if operational_committed is not None:
            if not callable(operational_committed):
                raise CausalJournalError("operational reread callback must be callable")
            contract.verify()
        run_id = contract.fields["run_id"] if operational_committed is not None else None
        replay = False
        with self.transaction(write=True) as connection:
            if (
                operational_committed is not None
                and connection.execute(
                    "SELECT 1 FROM lace_run_authority WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                is not None
            ):
                # A retry does not run the operational callback a second time.
                # Merely observing RUN_ADMITTED is not sufficient proof.
                self._verify_exact_causal_admission(
                    connection,
                    contract=contract,
                    receipt=receipt,
                    authenticator=authenticator,
                    control_epoch=control_epoch,
                    lease_identity=lease_identity,
                    operational_committed=operational_committed,
                )
                replay = True
            else:
                with self._guard_atomic_domain_participant(connection):
                    result = CausalRunJournal(connection).publish_admission_in(
                        connection,
                        contract,
                        receipt,
                        authenticator,
                        control_epoch=control_epoch,
                        lease_identity=lease_identity,
                        operational_admission=operational_admission,
                        before_commit=before_commit,
                    )
                if operational_committed is not None:
                    self._verify_exact_causal_admission(
                        connection,
                        contract=contract,
                        receipt=receipt,
                        authenticator=authenticator,
                        control_epoch=control_epoch,
                        lease_identity=lease_identity,
                        operational_committed=operational_committed,
                    )
        if operational_committed is None:
            return result
        # A new snapshot after COMMIT is mandatory: evidence inside the writer's
        # uncommitted transaction cannot establish durable persistence.
        with self.transaction() as connection:
            self._verify_exact_causal_admission(
                connection,
                contract=contract,
                receipt=receipt,
                authenticator=authenticator,
                control_epoch=control_epoch,
                lease_identity=lease_identity,
                operational_committed=operational_committed,
            )
        return CausalAdmissionCommit(
            "ALREADY_COMMITTED" if replay else "COMMITTED",
            cast(str, run_id),
            contract.identity,
            control_epoch,
            lease_identity,
        )

    def _verify_exact_causal_admission(
        self,
        connection: sqlite3.Connection,
        *,
        contract: AdmittedRunContract,
        receipt: AuthenticatedReceipt,
        authenticator: ReceiptAuthenticator,
        control_epoch: int,
        lease_identity: str,
        operational_committed: Callable[[sqlite3.Connection], bool],
    ) -> None:
        """Read-only proof for typed replay; never infer success from a unique error."""
        from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal

        row = connection.execute(
            """SELECT contract_identity, control_epoch,
            lease_identity, head_seq, state, admitted_contract, admission_receipt,
            admission_mac, admission_key_id, admission_domain
            FROM lace_run_authority WHERE run_id=?""",
            (contract.fields["run_id"],),
        ).fetchone()
        expected = (
            contract.identity,
            control_epoch,
            lease_identity,
            1,
            "ACTIVE",
            contract.encoded,
            receipt.payload,
            receipt.mac_hex,
            receipt.key_id,
            receipt.domain,
        )
        if row is None or tuple(row) != expected:
            raise CausalJournalError("idempotent replay is not the exact original admitted cut")
        events = CausalRunJournal(connection).verify(contract.fields["run_id"], auth=authenticator)
        if events != (
            {
                "run_seq": 1,
                "kind": "RUN_ADMITTED",
                "payload": {"contract_identity": contract.identity},
            },
        ):
            raise CausalJournalError("idempotent replay journal is not the exact admission")

        # A supplied operational proof is not allowed to manufacture the fact it
        # is meant to verify. Deny mutations within the caller-supplied readback.
        forbidden = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_PRAGMA,
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_ALTER_TABLE,
        }

        def read_only(
            action: int, _p1: str | None, _p2: str | None, _db: str | None, _source: str | None
        ) -> int:
            return sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK

        connection.set_authorizer(read_only)
        try:
            if operational_committed(connection) is not True:
                raise CausalJournalError("idempotent replay operational fact is not durably proven")
        finally:
            connection.set_authorizer(None)

    def durable_revision(self) -> int:
        with self.transaction() as connection:
            return read_durable_revision(connection)

    @contextmanager
    def admitted_observer_connection(self) -> Iterator[sqlite3.Connection]:
        """Hold one query-only admitted SQLite connection for bounded observation loops.

        Connection lifetime is reused for preparation performance, but each observation
        is a separate short read transaction.  That gives one SQLite snapshot/SHARED-lock
        cut for path+schema+lease+cancellation proof instead of letting a concurrent writer
        interleave page changes between autocommit statements.
        """
        if self._path_identity is None:
            raise RuntimeError(
                "admitted observer connection requires initialized database identity"
            )
        self._require_path_identity()
        connection: sqlite3.Connection | None = None
        last_identity_error: sqlite3.DatabaseError | None = None
        for attempt in range(ADMITTED_SQLITE_REPROOF_ATTEMPTS):
            try:
                with sqlite_authority_gate(self.path, write=False):
                    connection = self._open_admitted_observer_connection()
                    self._configure_common_connection(connection)
                    connection.execute("PRAGMA query_only = ON")
                    connection.set_authorizer(admitted_observer_authorizer)
                with self.admitted_observer_cut(connection, context="preparation observation"):
                    if last_identity_error is not None:
                        row = connection.execute("PRAGMA quick_check").fetchone()
                        if row is None or str(row[0]).lower() != "ok":
                            raise sqlite3.DatabaseError(
                                "SQLite quick_check failed during observer re-proof"
                            )
                        self._emit_recovery_event(
                            ExecutionRecoveryEvent(
                                kind="sqlite_reproof",
                                action="open_fresh_admitted_connection",
                                reason_code="transient_sqlite_identity_error",
                                outcome="succeeded",
                            )
                        )
                break
            except DatabasePathIdentityLost:
                if connection is not None:
                    connection.close()
                    connection = None
                raise
            except sqlite3.DatabaseError as exc:
                if connection is not None:
                    connection.close()
                    connection = None
                self._raise_if_path_identity_lost(exc)
                if _sqlite_identity_error(exc):
                    last_identity_error = exc
                    if attempt + 1 < ADMITTED_SQLITE_REPROOF_ATTEMPTS:
                        self._emit_recovery_event(
                            ExecutionRecoveryEvent(
                                kind="sqlite_reproof",
                                action="open_fresh_admitted_connection",
                                reason_code="transient_sqlite_identity_error",
                                outcome="requested",
                            )
                        )
                        delay = ADMITTED_SQLITE_REPROOF_DELAYS_SECONDS[attempt]
                        if delay:
                            time.sleep(delay)
                        continue
                    self._emit_recovery_event(
                        ExecutionRecoveryEvent(
                            kind="sqlite_reproof",
                            action="open_fresh_admitted_connection",
                            reason_code="persistent_sqlite_identity_error",
                            outcome="failed",
                        )
                    )
                    raise DatabasePathIdentityLost(
                        "database path no longer contains a readable admitted SQLite database "
                        f"after {ADMITTED_SQLITE_REPROOF_ATTEMPTS} bounded observation-only "
                        f"re-proof attempts during preparation observation: {self.path}: {exc} "
                        f"[sqlite_errorname={getattr(exc, 'sqlite_errorname', None)}, "
                        f"sqlite_errorcode={getattr(exc, 'sqlite_errorcode', None)}, "
                        f"path_observation={_database_path_observation(self.path, self._path_identity)}]"
                    ) from exc
                raise
        if connection is None:
            raise AssertionError("unreachable admitted observer connection loop")
        try:
            yield connection
        except sqlite3.DatabaseError as exc:
            self._raise_admitted_observer_error(exc)
            raise
        finally:
            connection.close()

    @contextmanager
    def admitted_observer_cut(
        self, connection: sqlite3.Connection, *, context: str = "preparation observation"
    ) -> Iterator[sqlite3.Connection]:
        """Own one atomic read-only proof cut on the persistent observer connection.

        BEGIN forces SQLite to establish one coherent read snapshot and SHARED lock for
        the whole authority observation.  Ending the transaction after every bounded
        callback keeps external cancellation writers visible on the next cadence.
        """

        self._require_path_identity()
        try:
            # Snapshot lifetime and the process-local revision floor are one authority cut.
            # The cross-process reader gate excludes Oh-Goon writers without competing for
            # SQLite RESERVED authority, so an already-reserved native writer can still
            # coexist with the observer until it reaches its own commit boundary.
            with sqlite_authority_gate(self.path, write=False), self._durable_revision_lock:
                connection.execute("PRAGMA shrink_memory")
                connection.execute("BEGIN")
                try:
                    self._prove_admitted_sqlite_identity(connection, context=context)
                    yield connection
                finally:
                    connection.rollback()
        except sqlite3.DatabaseError as exc:
            self._raise_admitted_observer_error(exc)
            raise

    def reprove_admitted_observer(self, connection: sqlite3.Connection) -> None:
        """Re-prove admitted identity through one short SQLite-owned read transaction."""

        with self.admitted_observer_cut(connection):
            pass

    def observe_admitted_read(
        self,
        observation: Callable[[sqlite3.Connection], T],
        *,
        context: str = "preparation observation",
    ) -> T:
        """Run one read-only authority observation with bounded fresh-handle re-proof.

        A format error raised *after* an admitted observer connection has opened is still
        only one SQLite observation of an unchanged dev/inode.  In rollback-journal mode
        that single observation must not destroy certification state.  Retry only the
        observation-only callback on a new non-creating read-only handle, then require the
        normal admitted identity proof plus quick_check before accepting recovery.

        Stable path replacement, schema/application-id/journal drift and durable revision
        rollback remain immediate fail-closed errors.  No Plan, Step, write, or external
        effect is replayed by this owner.
        """

        last_identity_error: sqlite3.DatabaseError | None = None
        for attempt in range(ADMITTED_SQLITE_REPROOF_ATTEMPTS):
            try:
                with self.admitted_observer_connection() as connection:
                    with self.admitted_observer_cut(connection, context=context):
                        result = observation(connection)
                    if last_identity_error is not None:
                        with self.admitted_observer_cut(connection, context=f"{context} recovery"):
                            row = connection.execute("PRAGMA quick_check").fetchone()
                            if row is None or str(row[0]).lower() != "ok":
                                raise sqlite3.DatabaseError(
                                    "SQLite quick_check failed during admitted observation re-proof"
                                )
                        self._emit_recovery_event(
                            ExecutionRecoveryEvent(
                                kind="sqlite_reproof",
                                action="open_fresh_admitted_connection",
                                reason_code="transient_sqlite_identity_error",
                                outcome="succeeded",
                            )
                        )
                    return result
            except DatabasePathIdentityLost as exc:
                cause = exc.__cause__
                # admitted_observer_connection already owns its own bounded opening re-proof.
                # Only a single-handle observation error is eligible for this outer replay.
                if (
                    not isinstance(cause, sqlite3.DatabaseError)
                    or not _sqlite_identity_error(cause)
                    or "after "
                    + str(ADMITTED_SQLITE_REPROOF_ATTEMPTS)
                    + " bounded observation-only re-proof attempts"
                    in str(exc)
                ):
                    raise
                last_identity_error = cause
                if attempt + 1 < ADMITTED_SQLITE_REPROOF_ATTEMPTS:
                    self._emit_recovery_event(
                        ExecutionRecoveryEvent(
                            kind="sqlite_reproof",
                            action="open_fresh_admitted_connection",
                            reason_code="transient_sqlite_identity_error",
                            outcome="requested",
                        )
                    )
                    delay = ADMITTED_SQLITE_REPROOF_DELAYS_SECONDS[attempt]
                    if delay:
                        time.sleep(delay)
                    continue
                self._emit_recovery_event(
                    ExecutionRecoveryEvent(
                        kind="sqlite_reproof",
                        action="open_fresh_admitted_connection",
                        reason_code="persistent_sqlite_identity_error",
                        outcome="failed",
                    )
                )
                raise DatabasePathIdentityLost(
                    "database path no longer contains a readable admitted SQLite database "
                    f"after {ADMITTED_SQLITE_REPROOF_ATTEMPTS} bounded observation-only "
                    f"re-proof attempts during {context}: {self.path}: {cause} "
                    f"[sqlite_errorname={getattr(cause, 'sqlite_errorname', None)}, "
                    f"sqlite_errorcode={getattr(cause, 'sqlite_errorcode', None)}, "
                    f"path_observation={_database_path_observation(self.path, self._path_identity)}]"
                ) from cause

        raise AssertionError("unreachable admitted observation re-proof loop")

    def _configure_common_connection(self, connection: sqlite3.Connection) -> None:
        configure_common_connection(self.path, connection)

    def _require_durable_connection_policy(self, connection: sqlite3.Connection) -> None:
        """Require SQLite's connection-local durability policy before every cut.

        This is a native SQLite observation, not a second transaction owner.
        Read-only observer handles are separately authorizer-fenced; operational
        transactions must not inherit a downgraded reusable write connection.
        """
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        if synchronous != 2:
            raise DatabasePathIdentityLost(
                "SQLite operational connection synchronous durability changed "
                f"after admission: expected FULL (2), observed {synchronous}"
            )
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if foreign_keys != 1:
            raise DatabasePathIdentityLost(
                "SQLite operational connection foreign key enforcement changed "
                f"after admission: expected ON (1), observed {foreign_keys}"
            )
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 0:
            raise DatabasePathIdentityLost(
                "SQLite operational connection unexpectedly became query-only after admission"
            )

    def _prove_admitted_sqlite_identity(
        self, connection: sqlite3.Connection, *, context: str
    ) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != DATABASE_SCHEMA_VERSION:
            raise DatabasePathIdentityLost(
                "database path no longer contains the admitted SQLite schema during "
                f"{context}: {self.path} "
                f"(expected user_version {DATABASE_SCHEMA_VERSION}, observed {version})"
            )
        schema_cookie = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        expected_cookie = self._schema_cookie
        if expected_cookie is not None and schema_cookie != expected_cookie:
            raise DatabasePathIdentityLost(
                "database schema identity changed after runtime admission during "
                f"{context}: {self.path} "
                f"(expected schema_version {expected_cookie}, observed {schema_cookie})"
            )
        observed_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        if observed_mode != self.journal_mode:
            raise DatabasePathIdentityLost(
                "database journal mode changed after runtime admission during "
                f"{context}: {self.path} "
                f"(expected {self.journal_mode}, observed {observed_mode})"
            )
        identity_cookie = int(connection.execute("PRAGMA application_id").fetchone()[0])
        expected_identity = self._database_identity_cookie
        if expected_identity is not None and identity_cookie != expected_identity:
            raise DatabasePathIdentityLost(
                "database instance identity changed after runtime admission during "
                f"{context}: {self.path} "
                f"(expected application_id {expected_identity}, observed {identity_cookie})"
            )
        # Durable-revision proof is one process-local authority cut.  Without this
        # synchronization a concurrent writer could publish a newer floor after this
        # connection observed SQLite but before the comparison, making a valid older
        # observation look like rollback (for example minimum 3, observed 2).
        with self._durable_revision_lock:
            admitted_floor = self._durable_revision_floor
            durable_revision = read_durable_revision(connection)
            if durable_revision < admitted_floor:
                raise DatabasePathIdentityLost(
                    "database durable revision moved backwards after runtime admission during "
                    f"{context}: {self.path} "
                    f"(minimum {admitted_floor}, observed {durable_revision})"
                )
            self._durable_revision_floor = max(admitted_floor, durable_revision)

    def _publish_durable_revision(self, committed_revision: int) -> None:
        """Publish a committed revision monotonically under the same proof cut."""

        with self._durable_revision_lock:
            self._durable_revision_floor = max(self._durable_revision_floor, committed_revision)

    def _raise_admitted_observer_error(self, exc: sqlite3.DatabaseError) -> None:
        self._raise_if_path_identity_lost(exc)
        if _sqlite_identity_error(exc):
            raise DatabasePathIdentityLost(
                "database path no longer contains a readable admitted SQLite database "
                f"during preparation observation: {self.path}: {exc}"
            ) from exc

    def _connect_with_context(self, context: str) -> sqlite3.Connection:
        previous = getattr(self._connection_scope, "open_context", None)
        self._connection_scope.open_context = context
        try:
            return self._connect()
        finally:
            self._connection_scope.open_context = previous

    def _prove_open_connection(
        self, connection: sqlite3.Connection, *, admitted: bool, context: str, recovered: bool
    ) -> None:
        if not admitted:
            return
        self._prove_admitted_sqlite_identity(connection, context=context)
        if not recovered:
            return
        row = connection.execute("PRAGMA quick_check").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError(
                "SQLite quick_check failed during admitted database re-proof"
            )
        self._emit_recovery_event(
            ExecutionRecoveryEvent(
                kind="sqlite_reproof",
                action="open_fresh_admitted_connection",
                reason_code="transient_sqlite_identity_error",
                outcome="succeeded",
            )
        )

    def _open_connection_attempt(
        self, *, admitted: bool, context: str, recovered: bool
    ) -> sqlite3.Connection:
        with sqlite_authority_gate(self.path, write=False):
            connection = self._open_sqlite_connection(admitted=admitted)
            try:
                self._configure_common_connection(connection)
                connection.execute("PRAGMA synchronous = FULL")
                if int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
                    raise sqlite3.DatabaseError(
                        "SQLite synchronous durability authority mismatch: expected FULL (2)"
                    )
                self._prove_open_connection(
                    connection, admitted=admitted, context=context, recovered=recovered
                )
                return connection
            except BaseException:
                connection.close()
                raise

    def _connect(self) -> sqlite3.Connection:
        context = getattr(self._connection_scope, "open_context", None) or "runtime connection"
        admitted = self._path_identity is not None
        attempts = ADMITTED_SQLITE_REPROOF_ATTEMPTS if admitted else 1
        last_identity_error: sqlite3.DatabaseError | None = None
        for attempt in range(attempts):
            if admitted:
                self._require_path_identity()
            try:
                return self._open_connection_attempt(
                    admitted=admitted, context=context, recovered=last_identity_error is not None
                )
            except DatabasePathIdentityLost:
                raise
            except sqlite3.DatabaseError as exc:
                self._raise_if_path_identity_lost(exc)
                if admitted and _sqlite_identity_error(exc):
                    last_identity_error = exc
                    if attempt + 1 < attempts:
                        self._emit_recovery_event(
                            ExecutionRecoveryEvent(
                                kind="sqlite_reproof",
                                action="open_fresh_admitted_connection",
                                reason_code="transient_sqlite_identity_error",
                                outcome="requested",
                            )
                        )
                        delay = ADMITTED_SQLITE_REPROOF_DELAYS_SECONDS[attempt]
                        if delay:
                            time.sleep(delay)
                        continue
                    self._emit_recovery_event(
                        ExecutionRecoveryEvent(
                            kind="sqlite_reproof",
                            action="open_fresh_admitted_connection",
                            reason_code="persistent_sqlite_identity_error",
                            outcome="failed",
                        )
                    )
                    raise DatabasePathIdentityLost(
                        "database path no longer contains a readable admitted SQLite database "
                        f"after {attempts} bounded observation-only re-proof attempts during {context}: "
                        f"{self.path}: {exc} "
                        f"[sqlite_errorname={getattr(exc, 'sqlite_errorname', None)}, "
                        f"sqlite_errorcode={getattr(exc, 'sqlite_errorcode', None)}, "
                        f"path_observation={_database_path_observation(self.path, self._path_identity)}]"
                    ) from exc
                raise
        raise AssertionError("unreachable admitted SQLite connection loop")

    def _open_sqlite_connection(self, *, admitted: bool) -> sqlite3.Connection:
        self.sqlite_diagnostics.record(
            getattr(self._connection_scope, "open_context", None) or "runtime connection",
            "native_opens",
        )
        if not admitted:
            return sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        # Never let sqlite3.connect create replacement bytes after runtime admission.
        # Path identity is checked immediately before every attempt, and mode=rw turns
        # a disappearance race into an error rather than silently creating a new file.
        uri = self.path.as_uri() + "?mode=rw"
        return sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)

    def _open_admitted_observer_connection(self) -> sqlite3.Connection:
        self.sqlite_diagnostics.record("preparation observation", "observer_opens")
        # Use a non-creating rw handle for rollback-journal observation. Native WSL
        # certification proved that a long-lived mode=ro pager can report SQLITE_NOTADB
        # while the admitted inode is being extended by a legitimate concurrent writer.
        # mode=rw lets SQLite participate in rollback-journal recovery/locking semantics.
        # The observer is still query-only and authorizer-fenced; the external reader gate
        # coordinates Oh-Goon writers without claiming SQLite RESERVED authority here.
        uri = self.path.as_uri() + "?mode=rw"
        return sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)

    def _require_path_identity(self) -> None:
        expected = self._path_identity
        if expected is None:
            return
        current = _capture_database_path_identity(self.path)
        if (current.device, current.inode) != (expected.device, expected.inode):
            raise DatabasePathIdentityLost(
                "database path was replaced after runtime admission: "
                f"{self.path} (expected dev/inode {expected.device}/{expected.inode}, "
                f"observed {current.device}/{current.inode})"
            )
        if current.filesystem != expected.filesystem:
            raise DatabasePathIdentityLost(
                "database filesystem admission changed while the runtime was active: "
                f"{self.path} (expected {expected.filesystem!r}, observed {current.filesystem!r})"
            )

    def _raise_if_path_identity_lost(self, exc: sqlite3.DatabaseError) -> None:
        try:
            self._require_path_identity()
        except DatabasePathIdentityLost as identity_error:
            raise identity_error from exc

    def _initialize(self) -> None:
        ensure_private_directory(self.path.parent)
        connection = self._connect()
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            existing_tables = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            fresh_database = version == 0 and existing_tables is None
            if version == 0 and existing_tables is not None:
                raise DatabaseSchemaMismatch(
                    "Unsupported unversioned pre-live database.\n"
                    + mismatch_message(
                        self.path,
                        found="unversioned pre-live database",
                        expected=DATABASE_SCHEMA_VERSION,
                    )
                )
            disposition = DATABASE_SCHEMA_POLICY.classify(version)
            if disposition not in {
                DurableSchemaDisposition.UNVERSIONED,
                DurableSchemaDisposition.CURRENT,
            }:
                detail = DATABASE_SCHEMA_POLICY.version_status_text(version)
                raise DatabaseSchemaMismatch(
                    detail
                    + "\n"
                    + mismatch_message(
                        self.path,
                        found=version,
                        expected=DATABASE_SCHEMA_VERSION,
                    )
                )
            identity_cookie = int(connection.execute("PRAGMA application_id").fetchone()[0])
            if not fresh_database and identity_cookie == 0:
                raise DatabaseSchemaMismatch(
                    "Current SQLite database is missing its required application_id identity"
                )
            observed_mode = str(
                connection.execute(f"PRAGMA journal_mode = {self.journal_mode}").fetchone()[0]
            ).upper()
            if observed_mode != self.journal_mode:
                raise sqlite3.DatabaseError(
                    "SQLite journal mode authority mismatch: "
                    f"requested {self.journal_mode}, observed {observed_mode}"
                )
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA_SQL)
            try:
                ensure_durable_revision_schema(connection)
                ensure_exclusion_schema(connection)
                connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
                connection.commit()
                if identity_cookie == 0:
                    identity_cookie = _new_database_identity_cookie()
                    connection.execute(f"PRAGMA application_id = {identity_cookie}")
                    observed_identity = int(
                        connection.execute("PRAGMA application_id").fetchone()[0]
                    )
                    if observed_identity != identity_cookie:
                        raise sqlite3.DatabaseError(
                            "SQLite database identity cookie could not be persisted"
                        )
                self._database_identity_cookie = identity_cookie
                self._schema_cookie = int(connection.execute("PRAGMA schema_version").fetchone()[0])
                with self._durable_revision_lock:
                    self._durable_revision_floor = read_durable_revision(connection)
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
        if os.name != "nt":
            self.path.chmod(0o600)
