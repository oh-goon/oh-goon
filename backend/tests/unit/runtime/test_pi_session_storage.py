"""Private Pi resource selection uses the physical Run and Step scope."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import IO, Any, cast

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    PiSessionCleanupUnverified,
    PiSessionHistoryUnqualified,
    PiSessionIdentityMismatch,
    PiSessionUnavailable,
)
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.pi_session_storage import (
    PI_SESSION_NEW_FILE,
    PiSessionDirectoryPin,
    PiSessionFileObservation,
    PiSessionSaveObservation,
    encode_pi_session_entry,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import pi_session_storage_id, repository_subject_instance_id
from oh_goon.engine.git import GitService
from oh_goon.runtime.pi_session_storage import (
    inspect_recovered_pi_session_storage,
    observe_pi_session_file,
    pin_pi_session_directory,
    pin_pi_session_retirement,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native directory custody")


def _require_live() -> None:
    """The selected fixture's original Run and Step remain current."""


@dataclass(frozen=True)
class _AdmittedScope:
    repository: RepositoryIdentity
    evidence: PlanRepositoryAdmissionEvidence
    layout: ApplicationStatePathLayout

    def create(
        self,
        *,
        workspace: Path | None = None,
        step_id: str = "pi",
        attempt_number: int = 1,
        require_current: Callable[[], None] = _require_live,
        storage_id: str | None = None,
        prior_file: PiSessionFileObservation | None = None,
    ) -> AbstractContextManager[PiSessionDirectoryPin]:
        return pin_pi_session_directory(
            layout=self.layout,
            fleet_run_id="fleet-original" if prior_file is None else "fleet-continuation",
            repository=self.repository,
            repository_admission=self.evidence,
            step_id=step_id,
            attempt_number=attempt_number,
            workspace=self.repository.path if workspace is None else workspace,
            require_current=require_current,
            storage_id=storage_id,
            prior_file=prior_file,
        )


@pytest.fixture
def admitted_pi_scope(tmp_path: Path) -> _AdmittedScope:
    repo = tmp_path / "repository"
    repo.mkdir()
    assert GitService().run("init", "-q", str(repo)).returncode == 0
    repository = RepositoryIdentity.model_validate({"path": repo})
    source = RepositorySourceAuthority.create(
        subject_instance_id=repository_subject_instance_id(repo),
        source_snapshot=PlanSnapshotEvidence(sha256="a" * 64, byte_count=0, entry_count=0),
    )
    evidence = PlanRepositoryAdmissionEvidence.create(
        repository_id=repository.repository_id,
        plan_id="admitted-plan",
        semantic_preview_id="fleet-plan-repository-preview-" + "b" * 64,
        active_step_ids=("pi",),
        source_authority=source,
    )
    layout = ApplicationStatePathLayout(tmp_path / "state")
    layout.root.mkdir(mode=0o700)
    return _AdmittedScope(repository, evidence, layout)


@pytest.fixture
def retained_pi_file(admitted_pi_scope: _AdmittedScope) -> tuple[Path, PiSessionFileObservation]:
    with admitted_pi_scope.create() as new:
        selected = new.path / PI_SESSION_NEW_FILE.name
        selected.write_bytes(b"retained\n")
        return selected, observe_pi_session_file(new, selected.name)


def test_new_private_slot_is_run_scoped_and_retained_on_failure(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    with pytest.raises(RuntimeError, match="original Step interrupted"):
        with admitted_pi_scope.create() as pin:
            assert pin.workspace == admitted_pi_scope.repository.path
            assert pin.path.parent == admitted_pi_scope.layout.pi_sessions
            assert (
                admitted_pi_scope.layout.pi_sessions
                in admitted_pi_scope.layout.private_control_paths
            )
            assert stat.S_IMODE(pin.path.stat().st_mode) == 0o700
            assert stat.S_IMODE(pin.path.parent.stat().st_mode) == 0o700
            assert os.fstat(pin.directory_fd).st_ino == pin.path.stat().st_ino
            pin.require_current()
            session = pin.path / "original.jsonl"
            session.write_bytes(b"Pi's own session bytes\n")
            metadata = observe_pi_session_file(pin, session.name)
            assert metadata.inode == session.stat().st_ino
            assert metadata.size == session.stat().st_size
            raise RuntimeError("original Step interrupted")
    assert session.read_bytes() == b"Pi's own session bytes\n"
    with pytest.raises(FleetPlanInvalid, match="already exists"), admitted_pi_scope.create():
        pytest.fail("Existing native storage cannot be adopted")
    with admitted_pi_scope.create(attempt_number=2) as next_attempt:
        assert next_attempt.path != session.parent
        assert tuple(next_attempt.path.iterdir()) == ()
    with pytest.raises(OSError):
        os.fstat(next_attempt.directory_fd)


def test_pi_persistence_refuses_unexpected_slot_entries_without_removing_them(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(b"native session bytes\n")
        unexpected = pin.path / "settings.json"
        unexpected.write_bytes(b'{"synthetic":"unexpected configuration"}\n')
        with pytest.raises(FleetPlanInvalid, match="contains unrelated entries"):
            observe_pi_session_file(pin, session.name)
    assert session.read_bytes() == b"native session bytes\n"
    assert unexpected.read_bytes() == b'{"synthetic":"unexpected configuration"}\n'


_NATIVE_SESSION_HEADER = b'{"type":"session","id":"original-session","version":3}\n'


def _native_save(*entries: dict[str, object]) -> PiSessionSaveObservation:
    leaf_id = entries[-1]["id"]
    assert isinstance(leaf_id, str)
    return PiSessionSaveObservation(
        session_id="original-session",
        leaf_id=leaf_id,
        entry_count=len(entries),
        entries_sha256=hashlib.sha256(
            b"".join(encode_pi_session_entry(entry) for entry in entries)
        ).hexdigest(),
    )


def test_qualified_pi_save_syncs_file_and_slot_and_preserves_exact_byte_receipt(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry: dict[str, object] = {"type": "message", "id": "original-leaf"}
    raw = _NATIVE_SESSION_HEADER + b'{"type": "message", "id": "original-leaf"}\n'
    native_fsync = os.fsync
    synchronized: list[tuple[int, int]] = []

    def record_sync(fd: int) -> None:
        info = os.fstat(fd)
        synchronized.append((info.st_dev, info.st_ino))
        native_fsync(fd)

    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(raw)
        with monkeypatch.context() as patched:
            patched.setattr(os, "fsync", record_sync)
            receipt = observe_pi_session_file(pin, session.name, _native_save(entry))
        slot_info = pin.path.stat()
        assert synchronized == [
            (receipt.device, receipt.inode),
            (slot_info.st_dev, slot_info.st_ino),
        ]
    assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.save_verification == "pi_rpc_synced"

    with admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt) as reopened:
        assert reopened.existing_file is not None
        reopened.existing_file.require_before_launch()
    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=session.parent.name,
        expected_file=receipt,
    ):
        assert session.read_bytes() == raw


@pytest.mark.parametrize("version", [2, 4, 3.0, "3"], ids=["legacy", "future", "float", "string"])
def test_pi_reopen_refuses_unqualified_format_even_with_matching_receipt(
    admitted_pi_scope: _AdmittedScope, version: int | float | str
) -> None:
    entry: dict[str, object] = {"type": "message", "id": "original-leaf"}
    raw = encode_pi_session_entry(
        {"type": "session", "id": "original-session", "version": version}
    ) + encode_pi_session_entry(entry)
    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(raw)
        receipt = observe_pi_session_file(pin, session.name, _native_save(entry))
    assert receipt.save_verification == "pi_rpc_synced"
    assert receipt.sha256 == hashlib.sha256(raw).hexdigest()

    with (
        pytest.raises(PiSessionHistoryUnqualified, match="format is unqualified"),
        admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt),
    ):
        pytest.fail("Unqualified Pi session format acquired writer custody")
    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=session.parent.name,
        expected_file=receipt,
    ):
        assert session.read_bytes() == raw


@pytest.mark.parametrize(
    "raw",
    [
        _NATIVE_SESSION_HEADER + b'{"type":"message","id":"original-leaf"}',
        _NATIVE_SESSION_HEADER + b'{"type":"message"\n',
        _NATIVE_SESSION_HEADER + b'{"type":"message","type":"message","id":"original-leaf"}\n',
        _NATIVE_SESSION_HEADER,
        _NATIVE_SESSION_HEADER + b'{"type":"message","id":"other-leaf"}\n',
        _NATIVE_SESSION_HEADER
        + b'{"type":"message","id":"original-leaf"}\n{"type":"message","id":"unexpected"}\n',
        b'{"type":"session","id":"foreign-session"}\n{"type":"message","id":"original-leaf"}\n',
    ],
    ids=(
        "missing-newline",
        "invalid-record",
        "ambiguous-record",
        "short-write",
        "wrong-extent",
        "extra-entry",
        "wrong-native-header",
    ),
)
def test_pi_save_refuses_unqualified_bytes_without_syncing_or_rewriting(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    entry: dict[str, object] = {"type": "message", "id": "original-leaf"}

    def unexpected_sync(_fd: int) -> None:
        pytest.fail("Unqualified bytes reached the positive save synchronization")

    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(raw)
        monkeypatch.setattr(os, "fsync", unexpected_sync)
        with pytest.raises(FleetPlanInvalid, match="Pi save"):
            observe_pi_session_file(pin, session.name, _native_save(entry))
    assert session.read_bytes() == raw


@pytest.mark.parametrize(
    ("target", "error_number"), [(stat.S_IFREG, errno.EIO), (stat.S_IFDIR, errno.ENOSPC)]
)
def test_pi_save_sync_failure_preserves_bytes_and_refuses_receipt(
    admitted_pi_scope: _AdmittedScope,
    monkeypatch: pytest.MonkeyPatch,
    target: int,
    error_number: int,
) -> None:
    entry: dict[str, object] = {"type": "message", "id": "original-leaf"}
    raw = _NATIVE_SESSION_HEADER + b'{"type":"message","id":"original-leaf"}\n'
    native_fsync = os.fsync
    faults: list[int] = []

    def fail_owned_sync(fd: int) -> None:
        if stat.S_IFMT(os.fstat(fd).st_mode) == target:
            faults.append(fd)
            raise OSError(error_number, "controlled Pi save synchronization failure")
        native_fsync(fd)

    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(raw)
        monkeypatch.setattr(os, "fsync", fail_owned_sync)
        with pytest.raises(FleetPlanInvalid, match="Original Pi session file is unavailable"):
            observe_pi_session_file(pin, session.name, _native_save(entry))
    assert len(faults) == 1
    assert session.read_bytes() == raw


def test_pi_save_close_failure_preserves_bytes_and_refuses_receipt(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry: dict[str, object] = {"type": "message", "id": "original-leaf"}
    raw = _NATIVE_SESSION_HEADER + b'{"type":"message","id":"original-leaf"}\n'
    native_open = os.open
    native_close = os.close
    observed_fd = None
    failed_close = False

    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(raw)

        def track_observed_file(
            path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> int:
            nonlocal observed_fd
            fd = native_open(path, flags, mode, dir_fd=dir_fd)
            if dir_fd == pin.directory_fd and path == session.name and flags & os.O_PATH:
                observed_fd = fd
            return fd

        def fail_observation_close(fd: int) -> None:
            nonlocal failed_close
            native_close(fd)
            if fd == observed_fd:
                failed_close = True
                raise OSError(errno.EIO, "controlled Pi save handle close failure")

        with monkeypatch.context() as patched:
            patched.setattr(os, "open", track_observed_file)
            patched.setattr(os, "close", fail_observation_close)
            with pytest.raises(FleetPlanInvalid, match="Original Pi session file is unavailable"):
                observe_pi_session_file(pin, session.name, _native_save(entry))
    assert failed_close
    assert session.read_bytes() == raw


@pytest.mark.parametrize("rewrite_prefix", [False, True])
def test_pi_continuation_save_qualifies_only_the_native_append_to_exact_predecessor(
    admitted_pi_scope: _AdmittedScope, rewrite_prefix: bool
) -> None:
    original: dict[str, object] = {"type": "message", "id": "original-leaf"}
    next_entry: dict[str, object] = {"type": "message", "id": "latest-leaf"}
    history = _NATIVE_SESSION_HEADER + b'{"type":"message","id":"original-leaf"}\n'
    appended = b'{"type":"message","id":"latest-leaf"}\n'
    with admitted_pi_scope.create() as pin:
        session = pin.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(history)
        predecessor = observe_pi_session_file(pin, session.name, _native_save(original))

    with admitted_pi_scope.create(storage_id=session.parent.name, prior_file=predecessor) as pin:
        prefix = history.replace(b"original-leaf", b"rewritten-one") if rewrite_prefix else history
        raw = prefix + appended
        session.write_bytes(raw)
        if rewrite_prefix:
            with pytest.raises(FleetPlanInvalid, match="changed its retained predecessor"):
                observe_pi_session_file(
                    pin, session.name, _native_save(next_entry), prior_file=predecessor
                )
        else:
            receipt = observe_pi_session_file(
                pin, session.name, _native_save(next_entry), prior_file=predecessor
            )
            assert receipt.save_verification == "pi_rpc_synced"
            assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
            assert (receipt.device, receipt.inode) == (predecessor.device, predecessor.inode)
    assert session.read_bytes() == raw


def test_rejects_foreign_workspace_inactive_step_and_stale_subject_before_writes(
    admitted_pi_scope: _AdmittedScope, tmp_path: Path
) -> None:
    scope = admitted_pi_scope
    alias = tmp_path / "repository-alias"
    alias.symlink_to(scope.repository.path, target_is_directory=True)
    for invalid in (
        scope.create(workspace=alias),
        scope.create(workspace=tmp_path / "foreign"),
        scope.create(step_id="inactive"),
    ):
        with pytest.raises(FleetPlanInvalid, match="admitted direct Git workspace"), invalid:
            pytest.fail("Foreign or inactive scope acquired storage")
    (scope.repository.path / "new-entry").touch()
    with pytest.raises(FleetPlanInvalid, match="subject changed"), scope.create():
        pytest.fail("Stale source subject acquired storage")
    assert not scope.layout.pi_sessions.exists()


def test_original_authority_refusal_precedes_directory_creation(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    def revoked() -> None:
        raise FleetPlanInvalid("Original Step is revoked")

    with (
        pytest.raises(FleetPlanInvalid, match="Original Step is revoked"),
        admitted_pi_scope.create(require_current=revoked),
    ):
        pytest.fail("Revoked Step acquired storage")
    assert not admitted_pi_scope.layout.pi_sessions.exists()


def test_excessive_pi_state_path_depth_refuses_before_storage_allocation(
    admitted_pi_scope: _AdmittedScope, tmp_path: Path
) -> None:
    deep_root = tmp_path.joinpath(*(["state"] * 32))
    deep_root.mkdir(mode=0o700, parents=True)
    layout = ApplicationStatePathLayout(deep_root)
    scope = _AdmittedScope(admitted_pi_scope.repository, admitted_pi_scope.evidence, layout)

    with pytest.raises(FleetPlanInvalid, match="bounded absolute native scope"), scope.create():
        pytest.fail("An over-depth state path acquired a private slot")

    assert tuple(deep_root.iterdir()) == ()


@pytest.mark.parametrize("topology", ["alias", "nested"])
def test_unqualified_native_mount_topology_refuses_before_storage_allocation(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch, topology: str
) -> None:
    mountinfo = Path("/proc/self/mountinfo")
    original_open = Path.open
    observed = mountinfo.read_bytes()
    if topology == "alias":
        root_device = Path("/").stat().st_dev
        device = f"{os.major(root_device)}:{os.minor(root_device)}".encode("ascii")
        mount_point = b"/elsewhere"
        diagnostic = "filesystem without bind aliases"
    else:
        device = b"4000:0"
        mount_point = os.fsencode(admitted_pi_scope.repository.path / "fixture-mount")
        diagnostic = "filesystem without nested mounts"
    row = b"2147483646 1 " + device + b" / " + mount_point + b" rw - ext4 /dev/fixture rw\n"

    def open_mountinfo(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path == mountinfo:
            return BytesIO(observed + row)
        return cast(IO[Any], original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", open_mountinfo)
    with pytest.raises(FleetPlanInvalid, match=diagnostic), admitted_pi_scope.create():
        pytest.fail("An unqualified mount topology acquired storage authority")
    assert not admitted_pi_scope.layout.pi_sessions.exists()


@pytest.mark.parametrize("alias", ["symlink", "world-readable"])
def test_private_storage_root_never_repairs_an_unproven_parent(
    admitted_pi_scope: _AdmittedScope, tmp_path: Path, alias: str
) -> None:
    root = admitted_pi_scope.layout.pi_sessions
    outside = tmp_path / "unrelated"
    outside.mkdir(mode=0o700)
    if alias == "symlink":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(mode=0o755)
    with pytest.raises(FleetPlanInvalid), admitted_pi_scope.create():
        pytest.fail("Unsafe storage parent acquired a writable slot")
    assert list(outside.iterdir()) == []
    if alias == "symlink":
        assert root.is_symlink()
    else:
        assert stat.S_IMODE(root.stat().st_mode) == 0o755
        assert list(root.iterdir()) == []


@pytest.mark.parametrize("personal_relative_path", [".pi", ".pi/agent"])
def test_canonical_personal_store_is_excluded_from_pi_storage(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    personal_relative_path: str,
) -> None:
    selected, receipt = retained_pi_file
    fixture_home = tmp_path / "fixture-home"
    personal_path = fixture_home / personal_relative_path
    canonical_storage = admitted_pi_scope.layout.pi_sessions
    original_resolve = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        if path == personal_path:
            return canonical_storage
        return original_resolve(path, strict=strict)

    monkeypatch.setenv("HOME", str(fixture_home))
    monkeypatch.setattr(Path, "resolve", resolve)
    with (
        pytest.raises(FleetPlanInvalid, match="overlaps default Pi personal storage"),
        admitted_pi_scope.create(attempt_number=2),
    ):
        pytest.fail("Personal storage acquired a new private slot")
    with (
        pytest.raises(FleetPlanInvalid, match="overlaps default Pi personal storage"),
        admitted_pi_scope.create(storage_id=selected.parent.name, prior_file=receipt),
    ):
        pytest.fail("Personal storage was opened for a continued write")
    with (
        pytest.raises(PiSessionIdentityMismatch),
        pin_pi_session_retirement(
            layout=admitted_pi_scope.layout,
            storage_id=selected.parent.name,
            expected_file=receipt,
        ),
    ):
        pytest.fail("Personal storage acquired retirement authority")
    assert tuple(canonical_storage.iterdir()) == (selected.parent,)
    assert selected.read_bytes() == b"retained\n"


def test_original_storage_handle_rejects_rename_and_replacement_at_launch(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    with admitted_pi_scope.create() as pin:
        moved = pin.path.with_name("retired")
        pin.path.rename(moved)
        pin.path.mkdir(mode=0o700)
        with pytest.raises(FleetPlanInvalid, match="changed before use"):
            pin.require_current()
        assert os.fstat(pin.directory_fd).st_ino == moved.stat().st_ino


def test_pi_last_use_requires_the_pinned_git_to_remain_repository_local(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    with admitted_pi_scope.create() as pin:
        pin.require_current()
        native_stat = os.stat
        fixture_marker = native_stat(admitted_pi_scope.repository.path / ".git/HEAD")

        def observe_git_marker(
            path: Any, *, dir_fd: int | None = None, follow_symlinks: bool = True
        ) -> os.stat_result:
            if path == "commondir" and dir_fd == pin.git_fd:
                assert follow_symlinks is False
                return fixture_marker
            return native_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(os, "stat", observe_git_marker)
        with pytest.raises(FleetPlanInvalid, match="requires repository-local Git metadata"):
            pin.require_current()


def test_post_settlement_observation_refuses_original_leaf_moved_to_replaced_parent(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    with admitted_pi_scope.create() as pin:
        (pin.path / "selected.jsonl").write_bytes(b"retained native bytes\n")
        old_parent = pin.path.parent.with_name("retired")
        pin.path.parent.rename(old_parent)
        pin.path.parent.mkdir(mode=0o700)
        (old_parent / pin.path.name).rename(pin.path)
        assert pin.path.stat().st_ino == os.fstat(pin.directory_fd).st_ino
        with pytest.raises(FleetPlanInvalid, match="changed before use"):
            observe_pi_session_file(pin, "selected.jsonl")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "missing"])
def test_native_metadata_refuses_aliases_without_touching_foreign_contents(
    admitted_pi_scope: _AdmittedScope, tmp_path: Path, kind: str
) -> None:
    foreign = tmp_path / "unrelated.jsonl"
    foreign.write_bytes(b"the original bytes\n")
    with admitted_pi_scope.create() as pin:
        selected = pin.path / "latest.jsonl"
        if kind == "symlink":
            selected.symlink_to(foreign)
        elif kind == "hardlink":
            os.link(foreign, selected)
        with pytest.raises(FleetPlanInvalid):
            observe_pi_session_file(pin, selected.name)
        with pytest.raises(FleetPlanInvalid, match="exact native basename"):
            observe_pi_session_file(pin, "../unrelated.jsonl")
    assert foreign.read_bytes() == b"the original bytes\n"


def test_retained_pi_file_reopens_and_records_a_new_digest_of_the_same_inode(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    old_history = b'{"type":"session"}\n'
    appended = b'{"type":"message"}\n'
    with admitted_pi_scope.create() as new:
        session = new.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(old_history)
        receipt = observe_pi_session_file(new, session.name)
    assert receipt.sha256 == hashlib.sha256(old_history).hexdigest()
    with admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt) as reopened:
        assert reopened.path == session.parent
        file = reopened.existing_file
        assert file is not None
        file.require_before_launch()
        assert os.fstat(file.fd).st_ino == receipt.inode
        with session.open("ab") as stream:
            stream.write(appended)
        after = observe_pi_session_file(reopened, session.name)
        assert (after.device, after.inode) == (receipt.device, receipt.inode)
        assert after.sha256 == hashlib.sha256(old_history + appended).hexdigest()
    with pytest.raises(OSError):
        os.fstat(file.fd)


@pytest.mark.parametrize(
    ("change", "failure"),
    [
        ("removed", PiSessionUnavailable),
        ("replaced", PiSessionIdentityMismatch),
        ("edited", PiSessionHistoryUnqualified),
        ("extra", PiSessionHistoryUnqualified),
        ("legacy", PiSessionHistoryUnqualified),
    ],
)
def test_exact_reopen_refuses_changed_or_unproven_original_file(
    admitted_pi_scope: _AdmittedScope, change: str, failure: type[PiSessionUnavailable]
) -> None:
    original = b"retained\n"
    with admitted_pi_scope.create() as new:
        session = new.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(original)
        receipt = observe_pi_session_file(new, session.name)
    if change == "removed":
        session.unlink()
    elif change == "replaced":
        replacement = session.with_suffix(".temporary")
        replacement.write_bytes(original)
        replacement.replace(session)
    elif change == "edited":
        before = session.stat()
        session.write_bytes(b"tampered\n")
        os.utime(session, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "extra":
        (session.parent / "models.json").write_bytes(b"unexpected template bytes\n")
    else:
        receipt = receipt.model_copy(update={"sha256": None})

    with (
        pytest.raises(failure),
        admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt),
    ):
        pytest.fail("Unproven retained Pi bytes acquired writer custody")
    if change == "removed":
        assert not session.exists()
    if change == "extra":
        assert session.read_bytes() == original
        assert (session.parent / "models.json").read_bytes() == b"unexpected template bytes\n"
    assert set(admitted_pi_scope.layout.pi_sessions.iterdir()) == {session.parent}


@pytest.mark.parametrize("operation", ["reopen", "retire"])
@pytest.mark.parametrize(
    ("field", "failure"),
    [("inode", PiSessionIdentityMismatch), ("size", PiSessionHistoryUnqualified)],
)
def test_admitted_pi_file_metadata_is_checked_before_any_readable_open(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    field: str,
    failure: type[PiSessionUnavailable],
) -> None:
    selected, receipt = retained_pi_file
    unqualified = receipt.model_copy(update={field: getattr(receipt, field) + 1})
    original_open = Path.open

    def reject_content_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path.parent == Path("/proc/self/fd"):
            pytest.fail("Unqualified Pi session acquired a readable descriptor")
        return cast(IO[Any], original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", reject_content_open)
    with pytest.raises(failure):
        if operation == "reopen":
            with admitted_pi_scope.create(storage_id=selected.parent.name, prior_file=unqualified):
                pytest.fail("Unqualified Pi session acquired writer custody")
        else:
            with pin_pi_session_retirement(
                layout=admitted_pi_scope.layout,
                storage_id=selected.parent.name,
                expected_file=unqualified,
            ):
                pytest.fail("Unqualified Pi session acquired retirement authority")


def test_retained_pi_file_is_rechecked_after_pin_before_native_launch(
    admitted_pi_scope: _AdmittedScope,
) -> None:
    with admitted_pi_scope.create() as new:
        session = new.path / PI_SESSION_NEW_FILE.name
        session.write_bytes(b"original\n")
        receipt = observe_pi_session_file(new, session.name)
    with admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt) as reopened:
        file = reopened.existing_file
        assert file is not None
        session.write_bytes(b"modified\n")
        with pytest.raises(PiSessionHistoryUnqualified, match="changed before launch"):
            file.require_before_launch()


def test_pi_reopen_checks_slot_membership_at_launch_and_after_native_exit(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
) -> None:
    session, receipt = retained_pi_file
    unexpected = session.parent / "late-entry"
    with admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt) as reopened:
        file = reopened.existing_file
        assert file is not None
        file.require_before_launch()
        unexpected.write_bytes(b"retain for explicit recovery\n")
        with pytest.raises(PiSessionIdentityMismatch, match="identity changed before launch"):
            file.require_before_launch()
        with pytest.raises(FleetPlanInvalid, match="contains unrelated entries"):
            observe_pi_session_file(reopened, session.name)
    assert session.read_bytes() == b"retained\n"
    assert unexpected.read_bytes() == b"retain for explicit recovery\n"


def test_recovered_pi_inspection_reports_only_exact_slot_occupancy(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = admitted_pi_scope
    storage_id = pi_session_storage_id("fleet-original", scope.repository.repository_id, "pi", 1)
    selected = scope.layout.pi_sessions / storage_id / PI_SESSION_NEW_FILE.name

    def inspect() -> str:
        return inspect_recovered_pi_session_storage(layout=scope.layout, storage_id=storage_id)

    assert inspect() == "unavailable"
    assert not scope.layout.pi_sessions.exists()
    with scope.create(attempt_number=2) as sibling:
        unrelated = sibling.path / PI_SESSION_NEW_FILE.name
        unrelated.write_bytes(b"unrelated Pi session")
    assert inspect() == "unavailable"
    assert not selected.parent.exists()
    with scope.create() as pin:
        assert pin.path == selected.parent
    assert inspect() == "unavailable"
    assert tuple(selected.parent.iterdir()) == ()

    native_open = os.open
    path_open = Path.open

    def metadata_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path in (selected, selected.name, str(selected)):
            assert flags & os.O_PATH
        return native_open(path, flags, *args, **kwargs)

    def refuse_contents(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        assert path.parent != Path("/proc/self/fd") and path != selected
        return cast(IO[Any], path_open(path, *args, **kwargs))

    for payload in (b"", b'{"partial":'):
        selected.write_bytes(payload)
        before = selected.stat()
        with monkeypatch.context() as patched:
            patched.setattr(os, "open", metadata_open)
            patched.setattr(Path, "open", refuse_contents)
            assert inspect() == "orphaned"
        assert selected.read_bytes() == payload
        after = selected.stat()
        assert os.path.samestat(before, after) and before.st_mtime_ns == after.st_mtime_ns
    assert unrelated.read_bytes() == b"unrelated Pi session"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "special", "extra"])
def test_recovered_pi_inspection_refuses_ambiguous_occupancy(
    admitted_pi_scope: _AdmittedScope, tmp_path: Path, kind: str
) -> None:
    scope = admitted_pi_scope
    other = tmp_path / "unrelated"
    other.write_bytes(b"unrelated private bytes")
    with scope.create() as pin:
        selected = pin.path / PI_SESSION_NEW_FILE.name
        if kind == "symlink":
            selected.symlink_to(other)
        elif kind == "hardlink":
            os.link(other, selected)
        elif kind == "special":
            os.mkfifo(selected)
        else:
            selected.touch()
            (pin.path / "unrelated-entry").touch()
    entries = {item.name: item.lstat().st_ino for item in pin.path.iterdir()}
    assert (
        inspect_recovered_pi_session_storage(layout=scope.layout, storage_id=pin.path.name)
        == "unverified"
    )
    assert {item.name: item.lstat().st_ino for item in pin.path.iterdir()} == entries
    assert other.read_bytes() == b"unrelated private bytes"


def test_retirement_unlinks_one_exact_pi_file_without_touching_other_storage(
    admitted_pi_scope: _AdmittedScope, retained_pi_file: tuple[Path, PiSessionFileObservation]
) -> None:
    selected, receipt = retained_pi_file
    with admitted_pi_scope.create(attempt_number=2) as sibling:
        unrelated = sibling.path / PI_SESSION_NEW_FILE.name
        unrelated.write_bytes(b"another Pi session\n")
    with (
        selected.open("rb") as original,
        pin_pi_session_retirement(
            layout=admitted_pi_scope.layout,
            storage_id=selected.parent.name,
            expected_file=receipt,
        ) as retirement,
    ):
        assert selected.exists()
        retirement.remove()
        assert os.fstat(original.fileno()).st_nlink == 0
    assert list(selected.parent.iterdir()) == []
    assert unrelated.read_bytes() == b"another Pi session\n"


def test_interrupted_retirement_verifies_absence_only_under_the_prepared_directory(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected, receipt = retained_pi_file

    def interrupted(_fd: int) -> None:
        raise InterruptedError("controlled interruption after unlink")

    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=selected.parent.name,
        expected_file=receipt,
    ) as pending:
        prepared = pending.directory
        with monkeypatch.context() as patched:
            patched.setattr(os, "fsync", interrupted)
            with pytest.raises(PiSessionCleanupUnverified):
                pending.remove()
    assert not selected.exists()

    with (
        pytest.raises(PiSessionUnavailable, match="absent before retirement"),
        pin_pi_session_retirement(
            layout=admitted_pi_scope.layout,
            storage_id=selected.parent.name,
            expected_file=receipt,
        ),
    ):
        pytest.fail("File absence without durable directory proof was accepted")
    synced = []
    real_fsync = os.fsync

    def record_directory_sync(fd: int) -> None:
        info = os.fstat(fd)
        assert stat.S_ISDIR(info.st_mode)
        synced.append((info.st_dev, info.st_ino))
        real_fsync(fd)

    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=selected.parent.name,
        expected_file=receipt,
        expected_directory=prepared,
    ) as retry:
        with monkeypatch.context() as patched:
            patched.setattr(os, "fsync", record_directory_sync)
            retry.remove()
    assert synced == [(prepared.device, prepared.inode)]

    moved = selected.parent.with_name("moved")
    selected.parent.rename(moved)
    selected.parent.mkdir(mode=0o700)
    with (
        pytest.raises(PiSessionIdentityMismatch, match="directory changed"),
        pin_pi_session_retirement(
            layout=admitted_pi_scope.layout,
            storage_id=selected.parent.name,
            expected_file=receipt,
            expected_directory=prepared,
        ),
    ):
        pytest.fail("A replacement empty slot was accepted on retry")


@pytest.mark.parametrize("change", ["symlink", "hardlink", "extra", "edited", "replaced", "legacy"])
def test_retirement_refuses_unqualified_or_unrelated_bytes_without_mutation(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
    tmp_path: Path,
    change: str,
) -> None:
    selected, receipt = retained_pi_file
    unrelated = tmp_path / "foreign"
    unrelated.write_bytes(b"retained\n")
    if change == "symlink":
        selected.unlink()
        selected.symlink_to(unrelated)
    elif change == "hardlink":
        unrelated.unlink()
        os.link(selected, unrelated)
    elif change == "extra":
        (selected.parent / "unexpected.txt").write_bytes(b"leave untouched\n")
    elif change == "edited":
        selected.write_bytes(b"modified\n")
    elif change == "replaced":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"retained\n")
        replacement.replace(selected)
    else:
        receipt = receipt.model_copy(update={"sha256": None})
    entries = {
        entry.name: (entry.lstat().st_ino, entry.read_bytes())
        for entry in selected.parent.iterdir()
    }
    with (
        pytest.raises(PiSessionUnavailable),
        pin_pi_session_retirement(
            layout=admitted_pi_scope.layout,
            storage_id=selected.parent.name,
            expected_file=receipt,
        ),
    ):
        pytest.fail("An unproved private file acquired deletion authority")
    assert {
        entry.name: (entry.lstat().st_ino, entry.read_bytes())
        for entry in selected.parent.iterdir()
    } == entries
    assert unrelated.read_bytes() == b"retained\n"


def test_retirement_rechecks_late_file_replacement_before_unlink(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
    tmp_path: Path,
) -> None:
    selected, receipt = retained_pi_file
    earlier = tmp_path / "preserved-original"
    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=selected.parent.name,
        expected_file=receipt,
    ) as retirement:
        selected.rename(earlier)
        selected.write_bytes(b"unrelated replacement\n")
        with pytest.raises(PiSessionIdentityMismatch, match="storage changed"):
            retirement.remove()
    assert earlier.read_bytes() == b"retained\n"
    assert selected.read_bytes() == b"unrelated replacement\n"


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_retirement_rechecks_slot_path_before_using_the_pinned_file(
    admitted_pi_scope: _AdmittedScope,
    retained_pi_file: tuple[Path, PiSessionFileObservation],
    tmp_path: Path,
    replacement: str,
) -> None:
    selected, receipt = retained_pi_file
    original = tmp_path / "moved-session"
    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=selected.parent.name,
        expected_file=receipt,
    ) as retirement:
        selected.parent.rename(original)
        if replacement == "symlink":
            selected.parent.symlink_to(original, target_is_directory=True)
        else:
            selected.parent.mkdir(mode=0o700)
            selected.write_bytes(b"another session\n")
        with pytest.raises(PiSessionUnavailable):
            retirement.remove()
    assert (original / selected.name).read_bytes() == b"retained\n"
    assert selected.read_bytes() == (
        b"retained\n" if replacement == "symlink" else b"another session\n"
    )
