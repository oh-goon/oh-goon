"""B27: one original Bubblewrap source-FD lease, NOT another execution owner.

Pin named bind sources before native allocation and mount those same objects with
Bubblewrap's native --[ro-]bind-fd options. This is launch-side provenance,
not signed kernel identity, physical observation, or teardown attestation.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.original_mount_intent import require_unshadowed_agent_mount_targets
from oh_goon.integrations.fleet_plan_local_lace_intent import prepared_original_native_mounts
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest

_BIND = {"--bind": "--bind-fd", "--ro-bind": "--ro-bind-fd"}
_AGENT_TARGET = "/opt/oh-goon/agent-source"


def _agent_sealed_descendants(
    source_fd: int,
    *,
    require_kernel_file_verity: bool = False,
    require_kernel_directory_immutable: bool = False,
) -> str:
    """Bounded no-symlink FD walk of the original staged Agent tree.

    Rechecked at native argv consumption; this detects post-pin edits, but
    cannot establish kernel immutability against a same-UID hostile writer.
    """
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    root_fd = os.open(".", flags | os.O_DIRECTORY, dir_fd=source_fd)
    entries = 0
    total_bytes = 0
    content = hashlib.sha256()

    def visit(fd: int, prefix: str) -> None:
        nonlocal entries, total_bytes
        before = os.fstat(fd)
        if (
            not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o500
        ):
            raise FleetPlanInvalid("Agent staged source directory is not sealed")
        if require_kernel_directory_immutable:
            from oh_goon.integrations.fleet_plan_agent_dir_immutable import (  # noqa: PLC0415
                require_original_agent_directory_immutable,
            )

            require_original_agent_directory_immutable(fd)
        for name in sorted(os.listdir(fd)):
            if not name or name in {".", ".."} or "/" in name or "\x00" in name:
                raise FleetPlanInvalid("Unsafe Agent staged source entry")
            entries += 1
            if entries > 16384:
                raise FleetPlanInvalid("Agent staged source tree exceeds bound")
            entry_fd = os.open(name, flags, dir_fd=fd)
            try:
                first = os.fstat(entry_fd)
                relative = prefix + "/" + name if prefix else name
                if stat.S_ISDIR(first.st_mode):
                    visit(entry_fd, relative)
                elif stat.S_ISREG(first.st_mode):
                    if (
                        first.st_nlink != 1
                        or first.st_uid != os.geteuid()
                        or stat.S_IMODE(first.st_mode) != 0o400
                    ):
                        raise FleetPlanInvalid("Agent staged file is not singly owned and sealed")
                    total_bytes += first.st_size
                    if total_bytes > 80 * 1024 * 1024:
                        raise FleetPlanInvalid("Agent staged source bytes exceed bound")
                    member = hashlib.sha256()
                    with os.fdopen(os.dup(entry_fd), "rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            member.update(chunk)
                    if require_kernel_file_verity:
                        from oh_goon.integrations.fleet_plan_agent_fsverity import (  # noqa: PLC0415
                            measure_original_agent_file_verity,
                        )

                        content.update(measure_original_agent_file_verity(entry_fd))
                    second = os.fstat(entry_fd)
                    if (
                        first.st_dev,
                        first.st_ino,
                        first.st_size,
                        first.st_mtime_ns,
                        first.st_ctime_ns,
                    ) != (
                        second.st_dev,
                        second.st_ino,
                        second.st_size,
                        second.st_mtime_ns,
                        second.st_ctime_ns,
                    ):
                        raise FleetPlanInvalid("Agent staged file changed during FD readback")
                    content.update(relative.encode("utf-8") + b"\0")
                    content.update(str(first.st_size).encode("ascii") + b"\0")
                    content.update(member.digest())
                    content.update(
                        repr(
                            (first.st_dev, first.st_ino, first.st_mtime_ns, first.st_ctime_ns)
                        ).encode("ascii")
                    )
                else:
                    raise FleetPlanInvalid("Nonregular Agent staged source entry")
            finally:
                os.close(entry_fd)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise FleetPlanInvalid("Agent staged directory changed during FD readback")
        content.update(b"D" + prefix.encode("utf-8") + b"\0")
        content.update(
            repr((before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)).encode(
                "ascii"
            )
        )

    try:
        visit(root_fd, "")
        if entries < 4:
            raise FleetPlanInvalid("Agent staged source tree incomplete")
        return content.hexdigest()
    except OSError as exc:
        raise FleetPlanInvalid("Unable to verify sealed Agent staged descendants") from exc
    finally:
        os.close(root_fd)


def _original_bwrap_options(args: list[str]) -> list[str]:
    """Parse mount switches only before Bubblewrap's child-command delimiter.

    The original mount builder calls this before appending the Step command;
    the final FD handoff calls it on the completed argv. Child arguments are
    *never* native resource declarations, even if they resemble mount flags.
    """
    if not args or any(type(arg) is not str for arg in args):
        raise FleetPlanInvalid("Malformed original Bubblewrap argv")
    return args[: args.index("--")] if "--" in args else args


def require_bubblewrap_fd_binding(executable: str) -> None:
    """Fail BEFORE allocation when the selected real bwrap lacks FD bind support."""
    if os.name != "posix" or not hasattr(os, "O_PATH"):
        raise FleetPlanInvalid("Linux O_PATH is required for kernel-pinned bind sources")
    try:
        completed = subprocess.run(
            (executable, "--help"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FleetPlanInvalid("Bubblewrap FD-bind capability could not be verified") from exc
    if (
        completed.returncode != 0
        or "--bind-fd" not in completed.stdout
        or "--ro-bind-fd" not in completed.stdout
    ):
        raise FleetPlanInvalid(
            "Bubblewrap requires --bind-fd and --ro-bind-fd; no pathname fallback"
        )


def _open_absolute_no_symlinks(source: str) -> int:
    """Open each component with O_NOFOLLOW; reject even ancestor symlinks."""
    if (
        not isinstance(source, str)
        or not source.startswith("/")
        or "\x00" in source
        or "//" in source
        or (source != "/" and source.endswith("/"))
        or any(p in (".", "..") for p in source.split("/"))
    ):
        raise FleetPlanInvalid("Noncanonical mount source path")
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in source.split("/")[1:]:
            if not part:
                continue
            next_fd = os.open(
                part,
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            if stat.S_ISLNK(info.st_mode):
                raise FleetPlanInvalid("Symlink in mount source path")
        if not (stat.S_ISREG(os.fstat(fd).st_mode) or stat.S_ISDIR(os.fstat(fd).st_mode)):
            raise FleetPlanInvalid("Mount source is not a regular file or directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True, slots=True)
class PinnedBindSource:
    option: str
    source: str
    target: str
    fd: int
    device: int
    inode: int
    mode: int
    agent_tree_digest: str | None = None
    kernel_file_verity_required: bool = False
    kernel_directory_immutable_required: bool = False


@dataclass(slots=True)
class OriginalBindSourceLease:
    """Owned FD roster for exactly the original signed, prepared bind targets."""

    pins: list[PinnedBindSource] = field(default_factory=list)
    _closed: bool = False

    @classmethod
    def pin_prepared(
        cls,
        request: IsolatedProcessRequest,
        *,
        require_agent_kernel_file_verity: bool = False,
        require_agent_kernel_directory_immutable: bool = False,
    ) -> OriginalBindSourceLease:
        attempt = request.attempt
        specs = [(m.mode, m.source, m.target) for m in prepared_original_native_mounts(request)]
        specs += [
            ("--ro-bind", str(b.snapshot_path), str(b.sandbox_path))
            for b in attempt.sandbox_bindings
        ]
        for tool in attempt.host_tools:
            specs.append(
                ("--ro-bind", str(tool.execution_snapshot_path), f"/toolchain/bin/{tool.name}")
            )
            if tool.name == "sh":
                specs.append(("--ro-bind", str(tool.execution_snapshot_path), "/bin/sh"))
        lease = cls()
        try:
            if require_agent_kernel_directory_immutable and not require_agent_kernel_file_verity:
                raise FleetPlanInvalid("Kernel directory custody also requires file fs-verity")
            if (
                require_agent_kernel_file_verity
                and getattr(request, "agent_source_mount", None) is None
            ):
                raise FleetPlanInvalid("Kernel fs-verity Agent source mount required")
            if getattr(request, "agent_source_mount", None) is not None:
                # Reuse the original signed descriptor's namespace rule at
                # the actual FD pinning cut. No native bind can overlay Agent
                # source after its read-only parent bind was admitted.
                try:
                    require_unshadowed_agent_mount_targets(
                        tuple(target for _, _, target in specs),
                        request.agent_source_mount.target,
                    )
                except ValueError as exc:
                    raise FleetPlanInvalid(str(exc)) from exc
            if len(specs) > 128 or len({target for _, _, target in specs}) != len(specs):
                raise FleetPlanInvalid("Ambiguous original bind source roster")
            for option, source, target in specs:
                lease._pin(
                    option,
                    source,
                    target,
                    require_kernel_file_verity=require_agent_kernel_file_verity
                    and target == _AGENT_TARGET,
                    require_kernel_directory_immutable=require_agent_kernel_directory_immutable
                    and target == _AGENT_TARGET,
                )
            return lease
        except BaseException:
            lease.close()
            raise

    def _pin(
        self,
        option: str,
        source: str,
        target: str,
        *,
        require_kernel_file_verity: bool = False,
        require_kernel_directory_immutable: bool = False,
    ) -> None:
        if self._closed or option not in _BIND or any(x.target == target for x in self.pins):
            raise FleetPlanInvalid("Invalid or duplicate mount source custody")
        try:
            fd = _open_absolute_no_symlinks(source)
        except OSError as exc:
            raise FleetPlanInvalid("Unable to pin original mount source before effects") from exc
        info = os.fstat(fd)
        try:
            tree_digest = (
                _agent_sealed_descendants(
                    fd,
                    require_kernel_file_verity=require_kernel_file_verity,
                    require_kernel_directory_immutable=require_kernel_directory_immutable,
                )
                if target == _AGENT_TARGET
                else None
            )
        except BaseException:
            os.close(fd)
            raise
        self.pins.append(
            PinnedBindSource(
                option,
                source,
                target,
                fd,
                info.st_dev,
                info.st_ino,
                info.st_mode,
                tree_digest,
                require_kernel_file_verity,
                require_kernel_directory_immutable,
            )
        )

    def pin_dynamic_cgroup(self, root: Path) -> None:
        """Pin the original resource owner's dynamic path, never mint a new path."""
        self._pin("--bind", str(root), "/sys/fs/cgroup")

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return tuple(x.fd for x in self.pins)

    def verify_kernel_custody(self) -> None:
        if self._closed or not self.pins or len(set(self.pass_fds)) != len(self.pins):
            raise FleetPlanInvalid("Original bind-source FD custody closed or overlapping")
        for source in self.pins:
            try:
                info = os.fstat(source.fd)
            except OSError as exc:
                raise FleetPlanInvalid("Original bind-source FD was closed") from exc
            if (info.st_dev, info.st_ino, info.st_mode) != (
                source.device,
                source.inode,
                source.mode,
            ):
                raise FleetPlanInvalid("Original bind-source kernel object substituted")
            try:
                # A reused number referencing the same inode via an ordinary
                # readable/writable descriptor is NOT the prepared O_PATH pin.
                # O_CLOEXEC is required: only run_command's explicit pass_fds
                # may cross the child boundary.
                flags = fcntl.fcntl(source.fd, fcntl.F_GETFL)
                fd_flags = fcntl.fcntl(source.fd, fcntl.F_GETFD)
            except OSError as exc:
                raise FleetPlanInvalid("Original bind-source FD flags unavailable") from exc
            if (flags & os.O_PATH) != os.O_PATH or not (fd_flags & fcntl.FD_CLOEXEC):
                raise FleetPlanInvalid("Original bind-source is not a cloexec O_PATH pin")
            if (
                source.agent_tree_digest is not None
                and _agent_sealed_descendants(
                    source.fd,
                    require_kernel_file_verity=source.kernel_file_verity_required,
                    require_kernel_directory_immutable=source.kernel_directory_immutable_required,
                )
                != source.agent_tree_digest
            ):
                raise FleetPlanInvalid("Original Agent staged descendants changed after FD pin")

    def consume_validated_argv(self, args: list[str]) -> None:
        """Replace only the previously validated original bind triplets."""
        self.verify_kernel_custody()
        options = _original_bwrap_options(args)
        locations = [
            (i, options[i], options[i + 1], options[i + 2])
            for i in range(len(options) - 2)
            if options[i] in _BIND
        ]
        expected = {(p.option, p.source, p.target): p for p in self.pins}
        if len(locations) != len(expected) or {
            (opt, src, target) for _, opt, src, target in locations
        } != set(expected):
            raise FleetPlanInvalid("Pinned source objects differ from original native mount argv")
        for i, option, source, target in locations:
            args[i] = _BIND[option]
            args[i + 1] = str(expected[option, source, target].fd)
        self.verify_fd_argv(args)

    def verify_fd_argv(self, args: list[str]) -> None:
        self.verify_kernel_custody()
        options = _original_bwrap_options(args)
        observed = [
            (options[i], options[i + 1], options[i + 2])
            for i in range(len(options) - 2)
            if options[i] in (*_BIND.values(), *_BIND)
        ]
        expected = [(_BIND[p.option], str(p.fd), p.target) for p in self.pins]
        if (
            sorted(observed) != sorted(expected)
            or len(observed) != len(expected)
            or any(opt in _BIND for opt, _, _ in observed)
            or len({target for _, _, target in observed}) != len(observed)
        ):
            raise FleetPlanInvalid(
                "Bubblewrap native FD bind argv differs from pinned source objects"
            )

    def release_pins_after_process(self) -> None:
        """Check and release the *local* native bind-source FD roster once.

        The original synchronous Bubblewrap owner calls this after its command
        supervisor returns, before reporting the CommandResult. All original
        pins are attempted even if one is broken; any uncertain close is a
        refusal rather than evidence of cleanup. This proves neither mount
        disappearance, foreign FD absence, process-tree quiescence, nor stage
        unsealing. On an exception before this cut, ordinary context cleanup
        remains best effort and cannot manufacture a teardown observation.
        """
        if self._closed or not self.pins:
            raise FleetPlanInvalid("Original native bind-source release requires live owned pins")
        failed = False
        try:
            self.verify_kernel_custody()
        except (FleetPlanInvalid, OSError):
            failed = True
        self._closed = True  # no second attempt can be mistaken for a verified release
        for pin in self.pins:
            try:
                os.close(pin.fd)
            except OSError:
                failed = True
                continue
            try:
                os.fstat(pin.fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    failed = True
            else:
                # A concurrently reused fd number is not a verified release.
                failed = True
        if failed:
            raise FleetPlanInvalid("Original native bind-source FD release not verified")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for pin in self.pins:
            try:
                os.close(pin.fd)
            except OSError:
                pass

    def __enter__(self) -> OriginalBindSourceLease:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
