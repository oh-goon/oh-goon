"""Private Pi resource selection uses the physical Run and Step scope."""

from __future__ import annotations

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
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.engine.git import GitService
from oh_goon.runtime.pi_session_storage import (
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


def test_native_mountinfo_alias_refuses_before_storage_allocation(
    admitted_pi_scope: _AdmittedScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountinfo = Path("/proc/self/mountinfo")
    original_open = Path.open
    observed = mountinfo.read_bytes()
    root_device = Path("/").stat().st_dev
    device = f"{os.major(root_device)}:{os.minor(root_device)}".encode("ascii")
    alias = b"2147483646 1 " + device + b" / /elsewhere rw - ext4 /dev/fixture rw\n"

    def open_mountinfo(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path == mountinfo:
            return BytesIO(observed + alias)
        return cast(IO[Any], original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", open_mountinfo)
    with (
        pytest.raises(FleetPlanInvalid, match="filesystem without bind aliases"),
        admitted_pi_scope.create(),
    ):
        pytest.fail("Native alias gained storage authority")
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
    else:
        receipt = receipt.model_copy(update={"sha256": None})

    with (
        pytest.raises(failure),
        admitted_pi_scope.create(storage_id=session.parent.name, prior_file=receipt),
    ):
        pytest.fail("Unproven retained Pi bytes acquired writer custody")
    if change == "removed":
        assert not session.exists()
    assert set(admitted_pi_scope.layout.pi_sessions.iterdir()) == {session.parent}


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
    with pin_pi_session_retirement(
        layout=admitted_pi_scope.layout,
        storage_id=selected.parent.name,
        expected_file=receipt,
        expected_directory=prepared,
    ) as retry:
        retry.remove()

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
