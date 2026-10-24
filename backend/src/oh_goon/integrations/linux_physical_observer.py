"""B12E-C1 single shared read-only Linux physical observation mechanism.

No original Run admission, resource effects, journal publication, physical
absence classification, or operator scope parsing here. Only kernel facts.
A visible negative observation is NOT a containment proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, cast

from oh_goon.core.ports.physical_observation import (
    ORIGINAL_KERNEL_COMPONENTS,
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
)
from oh_goon.core.ports.physical_observation import (
    PhysicalObservationScope as Scope,
)


def _read_regular(path: Path, limit: int) -> str:
    """Read bounded proc data, rejecting symlinks and short-lived substitution."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("non-regular proc observation")
        raw = os.read(fd, limit + 1)
        after = os.fstat(fd)
        if len(raw) > limit or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError("unbounded or changed proc observation")
        return raw.decode("utf-8", "strict")
    finally:
        os.close(fd)


def _birth(stat_content: str) -> str:
    # /proc/$pid/stat comm is parenthesized and may contain spaces or ')'.
    tail = stat_content.rfind(") ")
    if tail < 0:
        raise ValueError("malformed process stat")
    fields = stat_content[tail + 2 :].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError("malformed process birth")
    return fields[19]  # Linux stat field 22: starttime


def _command(argv: list[str]) -> Any:
    result = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5.0,
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    if result.returncode != 0 or len(result.stdout) > 1_048_576:
        raise OSError("native read-only inventory unavailable")
    return json.loads(result.stdout)


def _component(state: str, detail: str) -> dict[str, str]:
    return {"state": state, "detail": detail}


def _read_observed_cgroup_control(directory_fd: int, name: str, limit: int) -> str:
    """Read a diagnostic kernel control relative to the pinned original inode.

    This is read-only evidence, NOT an LACE effect or independent custody.
    """
    if name not in ("cgroup.events", "cgroup.procs"):
        raise ValueError("unexpected original cgroup control")
    control_fd = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=directory_fd
    )
    try:
        before = os.fstat(control_fd)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("non-regular cgroup control")
        raw = os.read(control_fd, limit + 1)
        after = os.fstat(control_fd)
        if len(raw) > limit or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError("unbounded or replaced original cgroup control")
        return raw.decode("ascii", "strict")
    finally:
        os.close(control_fd)


def _cgroup(scope: Scope) -> dict[str, str]:
    path = Path(scope.cgroup_path)
    try:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            return _component("UNKNOWN", "cgroup symlink / path substitution")
        try:
            st = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return _component("NOT_OBSERVED", "original path missing in local observation")
        if not stat.S_ISDIR(st.st_mode):
            return _component("UNKNOWN", "cgroup path is not a directory")
        expected = (scope.cgroup_device, scope.cgroup_inode)
        if (st.st_dev, st.st_ino) != expected:
            return _component("UNKNOWN", "original cgroup inode/device differs")
        # CO-R3S: opening both controls by absolute pathname after the first
        # identity check can inspect a foreign replacement. Pin one directory
        # FD and observe both controls relative to that SAME original inode.
        directory_fd = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            opened = os.fstat(directory_fd)
            if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != expected:
                return _component(
                    "UNKNOWN", "original cgroup directory replaced before observation"
                )
            events = _read_observed_cgroup_control(directory_fd, "cgroup.events", 4096).splitlines()
            procs = _read_observed_cgroup_control(directory_fd, "cgroup.procs", 65536).split()
            # A vanished or substituted pathname after the reads cannot be
            # presented as stable original-cgroup observation. The handle
            # still describes its inode, but not exclusive namespace custody.
            final = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(final.st_mode)
                or (final.st_dev, final.st_ino) != expected
                or (os.fstat(directory_fd).st_dev, os.fstat(directory_fd).st_ino) != expected
            ):
                return _component("UNKNOWN", "original cgroup path replaced during observation")
        finally:
            os.close(directory_fd)
        population = [x for x in events if x.startswith("populated ")]
        if len(population) != 1 or population[0] not in ("populated 0", "populated 1"):
            return _component("UNKNOWN", "malformed kernel cgroup population")
        if population[0] == "populated 1" or procs:
            return _component("PRESENT", "original cgroup exists with population")
        return _component("PRESENT", "original cgroup exists, reported empty")
    except (OSError, UnicodeError, ValueError):
        return _component("UNKNOWN", "cgroup observation incomplete or raced")


def _cgroup_subtree(scope: Scope, proc_root: Path = Path("/proc")) -> dict[str, str]:
    """Bounded original-subtree inventory, NOT descendant absence proof.

    All visited nodes must remain under the pinned original cgroup directory.
    An unreadable or racing child, unknown PID birth, symlink, or over-budget
    tree invalidates the ENTIRE observation. Never use this for effects.
    """
    root = Path(scope.cgroup_path)
    try:
        if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            return _component("UNKNOWN", "cgroup parent or root is a symlink")
        try:
            initial = root.stat(follow_symlinks=False)
        except FileNotFoundError:
            return _component(
                "NOT_OBSERVED", "pinned cgroup root not visible; detached resources unknown"
            )
        if not stat.S_ISDIR(initial.st_mode) or (initial.st_dev, initial.st_ino) != (
            scope.cgroup_device,
            scope.cgroup_inode,
        ):
            return _component("UNKNOWN", "original cgroup root identity changed")
        pending: list[tuple[Path, int]] = [(root, 0)]
        seen = 0
        members = 0
        populated = 0
        digest = hashlib.sha256()
        while pending:
            current, depth = pending.pop()
            seen += 1
            if seen > 256 or depth > 16:
                return _component("UNKNOWN", "subtree exceeds bounded observation budget")
            before = current.stat(follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_dev != initial.st_dev:
                return _component("UNKNOWN", "subtree directory identity changed")
            # cgroup.events populated includes descendants; cgroup.procs does not.
            events = _read_regular(current / "cgroup.events", 4096).splitlines()
            procs = _read_regular(current / "cgroup.procs", 65536).split()
            flags = [x for x in events if x.startswith("populated ")]
            if len(flags) != 1 or flags[0] not in ("populated 0", "populated 1"):
                return _component("UNKNOWN", "malformed cgroup subtree population")
            is_populated = flags[0] == "populated 1"
            if not is_populated and procs:
                return _component("UNKNOWN", "cgroup population changed during observation")
            if len(procs) + members > 1024 or any(not x.isdecimal() or int(x) < 1 for x in procs):
                return _component("UNKNOWN", "unbounded or invalid subtree PID inventory")
            member_identities = []
            boot_id = None
            if procs:
                boot_id = _read_regular(proc_root / "sys/kernel/random/boot_id", 128).strip()
                if boot_id != scope.tracked_birth.rpartition(":")[0]:
                    return _component(
                        "UNKNOWN", "subtree process boot identity differs from original"
                    )
            for value in sorted(procs, key=int):
                pid = int(value)
                identity = _birth(_read_regular(proc_root / str(pid) / "stat", 8192))
                member_identities.append(f"{boot_id}:{pid}:{identity}")
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
            children = []
            for entry in entries:
                if entry.is_symlink():
                    return _component("UNKNOWN", "symlink in delegated cgroup subtree")
                if entry.is_dir(follow_symlinks=False):
                    children.append(Path(entry.path))
            after = current.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                return _component("UNKNOWN", "cgroup subtree node replaced during scan")
            digest.update(
                json.dumps(
                    [
                        str(current.relative_to(root)),
                        before.st_dev,
                        before.st_ino,
                        is_populated,
                        member_identities,
                        [x.name for x in children],
                    ],
                    separators=(",", ":"),
                ).encode()
            )
            populated += int(is_populated)
            members += len(procs)
            pending.extend((child, depth + 1) for child in reversed(children))
        final = root.stat(follow_symlinks=False)
        if (initial.st_dev, initial.st_ino) != (final.st_dev, final.st_ino):
            return _component("UNKNOWN", "original cgroup root replaced during inventory")
        return _component(
            "PRESENT",
            f"subtree exists; nodes={seen}; populated_nodes={populated}; "
            f"observed_members={members}; inventory_sha256={digest.hexdigest()}",
        )
    except (OSError, UnicodeError, ValueError, OverflowError):
        return _component("UNKNOWN", "subtree observation incomplete, permission denied, or raced")


def _process(scope: Scope, proc_root: Path) -> dict[str, str]:
    path = proc_root / str(scope.tracked_pid) / "stat"
    try:
        current = _birth(_read_regular(path, 8192))
        boot_id = _read_regular(proc_root / "sys" / "kernel" / "random" / "boot_id", 128).strip()
        if f"{boot_id}:{current}" != scope.tracked_birth:
            return _component("UNKNOWN", "tracked PID was reused or birth changed")
        return _component("PRESENT", "original tracked process identity still alive")
    except FileNotFoundError:
        return (
            _component("NOT_OBSERVED", "tracked PID missing; other descendants remain unproven")
            if not (proc_root / str(scope.tracked_pid)).exists()
            else _component(
                "UNKNOWN", "tracked PID directory exists without readable stat/boot identity"
            )
        )
    except (OSError, UnicodeError, ValueError):
        return _component("UNKNOWN", "original process birth not observable")


def _mounts(scope: Scope, proc_root: Path) -> dict[str, str]:
    # /proc/self/mountinfo is only the observer's own mount namespace. It does
    # not enumerate detached mounts in another namespace or prove their absence.
    try:
        raw = _read_regular(proc_root / "self" / "mountinfo", 1_048_576)
        mount_points = set()
        for line in raw.splitlines():
            parts = line.split(" ")
            if len(parts) < 10 or "-" not in parts:
                return _component("UNKNOWN", "mountinfo malformed")
            mount_points.add(parts[4].replace("\\040", " "))
        if any(t in mount_points for t in scope.mount_targets):
            return _component("PRESENT", "target mount visible in observer namespace")
        return _component("NOT_OBSERVED", "no target in observer namespace; foreign mounts unknown")
    except (OSError, UnicodeError):
        return _component("UNKNOWN", "mount enumeration unavailable")


def _network(
    scope: Scope, *, command: Callable[[list[str]], object] | None = None
) -> dict[str, str]:
    if scope.network_namespace is None:
        return _component(
            "UNKNOWN", "no original network lease; containment scope still unverified"
        )
    if command is None:
        command = _command
    try:
        namespaces = command(["ip", "-j", "netns", "list"])
        links = command(["ip", "-j", "link", "show"])
        tables = command(["nft", "-j", "list", "tables"])
        if (
            type(namespaces) is not list
            or type(links) is not list
            or type(tables) is not dict
            or type(tables.get("nftables")) is not list
        ):
            return _component("UNKNOWN", "network inventory has invalid schema")
        match = (
            any(type(x) is dict and x.get("name") == scope.network_namespace for x in namespaces)
            or any(
                type(x) is dict
                and (
                    x.get("ifname") == scope.host_veth
                    or (
                        type(x.get("ifname")) is str
                        and x["ifname"].startswith(cast(str, scope.host_veth) + "@")
                    )
                )
                for x in links
            )
            or any(
                type(x) is dict
                and type(x.get("table")) is dict
                and x["table"].get("family") == "inet"
                and x["table"].get("name") == scope.nft_table
                for x in tables["nftables"]
            )
        )
        return _component(
            "PRESENT" if match else "NOT_OBSERVED",
            "original network resource visible"
            if match
            else "named network resources not listed; detached namespaces unknown",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return _component("UNKNOWN", "network observation unavailable")


def _mount_field(raw: str) -> str:
    """Decode only Linux mountinfo's four-digit octal escapes, fail closed."""
    if "\\" not in raw:
        return raw
    if re.search(r"\\(?![0-7]{3})", raw):
        raise ValueError("invalid mountinfo escape")
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), raw)


def _tracked_namespace_inventory(scope: Scope, proc_root: Path) -> dict[str, dict[str, str]]:
    """Development-only inventory of the exact tracked process's namespaces.

    This is NOT a census of other processes, detached mounts, namespace FDs,
    bind mounts in foreign namespaces or the original Run's complete resources.
    Every observation is associated with the pinned process boot/starttime;
    identity drift or incomplete proc reads invalidate both components.
    """
    names = ("tracked_mount_namespace", "tracked_network_namespace")

    def both(state: str, detail: str) -> dict[str, dict[str, str]]:
        return {name: _component(state, detail) for name in names}

    pid_dir = proc_root / str(scope.tracked_pid)
    if proc_root.is_symlink() or pid_dir.is_symlink():
        return both("UNKNOWN", "proc root or tracked PID directory is substituted")

    def pinned_starttime() -> str:
        stat_record = _read_regular(pid_dir / "stat", 8192)
        if stat_record.split(" (", 1)[0] != str(scope.tracked_pid):
            raise ValueError("proc stat PID does not match pinned tracked identity")
        return _birth(stat_record)

    try:
        before = pinned_starttime()
    except FileNotFoundError:
        if pid_dir.exists():
            return both("UNKNOWN", "tracked PID exists but stat is unavailable")
        return both("NOT_OBSERVED", "tracked process missing; foreign namespaces remain unknown")
    except (OSError, UnicodeError, ValueError):
        return both("UNKNOWN", "tracked process identity unreadable")
    try:
        boot = _read_regular(proc_root / "sys/kernel/random/boot_id", 128).strip()
        if f"{boot}:{before}" != scope.tracked_birth:
            return both("UNKNOWN", "tracked PID birth or boot differs from original")
        mnt_handle = os.readlink(pid_dir / "ns/mnt")
        net_handle = os.readlink(pid_dir / "ns/net")
        if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", mnt_handle) or not re.fullmatch(
            r"net:\[[1-9][0-9]*\]", net_handle
        ):
            return both("UNKNOWN", "invalid proc namespace handle identity")
        raw = _read_regular(pid_dir / "mountinfo", 1_048_576)
        records: list[tuple[int, int, str, str, str]] = []
        mount_ids: set[int] = set()
        targets = set(scope.mount_targets)
        matched_targets: dict[str, list[tuple[int, str, str]]] = {}
        for line in raw.splitlines():
            fields = line.split()
            if (
                len(fields) < 10
                or fields.count("-") != 1
                or fields.index("-") < 6
                or fields.index("-") + 3 > len(fields)
                or not fields[0].isdecimal()
                or not fields[1].isdecimal()
            ):
                raise ValueError("invalid mountinfo record")
            mount_id, parent_id = int(fields[0]), int(fields[1])
            if mount_id < 1 or parent_id < 1 or mount_id in mount_ids:
                raise ValueError("duplicate or invalid mount ID")
            if not re.fullmatch(r"[0-9]+:[0-9]+", fields[2]):
                raise ValueError("invalid mount device identity")
            mount_ids.add(mount_id)
            source_root, target = _mount_field(fields[3]), _mount_field(fields[4])
            records.append((mount_id, parent_id, fields[2], source_root, target))
            if target in targets:
                matched_targets.setdefault(target, []).append((mount_id, fields[2], source_root))
            if len(records) > 8192:
                raise ValueError("unbounded mount namespace inventory")
        if not records:
            raise ValueError("empty mount namespace inventory")
        after = pinned_starttime()
        if (
            after != before
            or os.readlink(pid_dir / "ns/mnt") != mnt_handle
            or os.readlink(pid_dir / "ns/net") != net_handle
        ):
            return both("UNKNOWN", "process or namespace identity changed during scan")
        fingerprint = hashlib.sha256(
            json.dumps([mnt_handle, sorted(records)], separators=(",", ":")).encode()
        ).hexdigest()
        matched = sorted(record[0] for record in records if record[4] in targets)
        # B27B2B3A: one shared Linux parser, no second LACE observation path.
        # Partial visibility or stacked mounts MUST NOT look like complete
        # original signed-target coverage. Kernel mount IDs and device/root
        # identities describe what this namespace exposes, not original FD
        # consumption, hidden tasks, teardown or independent custody.
        ambiguous = any(len(entries) != 1 for entries in matched_targets.values())
        missing = targets - matched_targets.keys()
        coverage = (
            "UNKNOWN"
            if ambiguous or (matched and missing)
            else "PRESENT"
            if matched
            else "NOT_OBSERVED"
        )
        target_fingerprint = hashlib.sha256(
            json.dumps(
                [
                    (target, matched_targets[target][0])
                    for target in sorted(matched_targets)
                    if len(matched_targets[target]) == 1
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        mounts = _component(
            coverage,
            f"tracked process mount namespace={mnt_handle}; inventory_sha256={fingerprint}; "
            f"target_mount_ids={matched}; visible_targets={len(matched_targets)}/{len(targets)}; "
            f"ambiguous_target_mounts={ambiguous}; target_kernel_sha256={target_fingerprint}; "
            "foreign/detached mounts unverified",
        )
        network = (
            _component(
                "PRESENT",
                f"tracked process network handle={net_handle}; "
                "original named network lease association and detached handles unverified",
            )
            if scope.network_namespace is not None
            else _component(
                "UNKNOWN", "no original named network lease; tracked net handle has no Run binding"
            )
        )
        return {"tracked_mount_namespace": mounts, "tracked_network_namespace": network}
    except (OSError, UnicodeError, ValueError, OverflowError):
        return both("UNKNOWN", "tracked namespace observation incomplete or raced")


def _namespace_fd_holders(scope: Scope, proc_root: Path) -> dict[str, str]:
    """Bounded read-only census of namespace process/FD holders in one /proc.

    This is deliberately NOT a proof of nonexistence: PID namespaces, unlisted
    tasks, foreign /proc mounts and non-FD kernel references are outside scope.
    A missing tracked PID means the original namespace handles cannot be
    established, and the observation is UNKNOWN rather than absent.
    """
    try:
        tracked = proc_root / str(scope.tracked_pid)
        if proc_root.is_symlink() or tracked.is_symlink():
            raise OSError("substituted proc root or tracked process")
        boot = _read_regular(proc_root / "sys/kernel/random/boot_id", 128).strip()
        if boot != scope.tracked_birth.rpartition(":")[0]:
            raise ValueError("boot mismatch")

        def original_identity() -> tuple[str, str]:
            stat_record = _read_regular(tracked / "stat", 8192)
            if stat_record.split(" (", 1)[0] != str(scope.tracked_pid):
                raise ValueError("tracked stat PID differs")
            if f"{boot}:{_birth(stat_record)}" != scope.tracked_birth:
                raise ValueError("tracked birth identity changed")
            mnt, net = (os.readlink(tracked / "ns" / name) for name in ("mnt", "net"))
            if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", mnt) or not re.fullmatch(
                r"net:\[[1-9][0-9]*\]", net
            ):
                raise ValueError("invalid original namespace identities")
            return mnt, net

        original_mnt, original_net = original_identity()
        with os.scandir(proc_root) as iterator:
            candidates = sorted(
                (
                    entry.name
                    for entry in iterator
                    if entry.name.isascii() and entry.name.isdecimal()
                ),
                key=int,
            )
        if len(candidates) > 512:
            raise ValueError("proc census exceeds 512-PID budget")
        digest = hashlib.sha256()
        matches: list[str] = []
        process_holders: list[str] = []
        scanned = 0
        for name in candidates:
            pid_dir = proc_root / name
            if pid_dir.is_symlink() or not pid_dir.is_dir():
                raise OSError("substituted or missing PID directory")
            start = _read_regular(pid_dir / "stat", 8192)
            if start.split(" (", 1)[0] != name:
                raise ValueError("PID stat identity mismatch")
            birth = _birth(start)
            fd_dir = pid_dir / "fd"
            if fd_dir.is_symlink():
                raise OSError("FD directory substituted")
            with os.scandir(fd_dir) as iterator:
                fds = sorted(
                    (
                        entry.name
                        for entry in iterator
                        if entry.name.isascii() and entry.name.isdecimal()
                    ),
                    key=int,
                )
            if len(fds) > 256:
                raise ValueError("per-process FD budget exceeded")
            holder_fd: list[str] = []
            fd_links: dict[str, str] = {}
            pid_mnt, pid_net = (os.readlink(pid_dir / "ns" / kind) for kind in ("mnt", "net"))
            if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", pid_mnt) or not re.fullmatch(
                r"net:\[[1-9][0-9]*\]", pid_net
            ):
                raise ValueError("invalid process namespace handles")
            if pid_mnt == original_mnt or pid_net == original_net:
                process_holders.append(f"{name}:{birth}:{pid_mnt}:{pid_net}")
            for fd in fds:
                target = os.readlink(fd_dir / fd)
                fd_links[fd] = target
                if target.startswith(("mnt:[", "net:[")) and not re.fullmatch(
                    r"(?:mnt|net):\[[1-9][0-9]*\]", target
                ):
                    raise ValueError("malformed namespace FD handle")
                if target in (original_mnt, original_net):
                    holder_fd.append(f"{fd}:{target}")
            # A descriptor can be closed and reused without changing the PID
            # or namespace. Recheck BOTH names and targets before claiming any
            # visible holder, including scoped non-detection.
            with os.scandir(fd_dir) as iterator:
                final_fds = sorted(
                    (
                        entry.name
                        for entry in iterator
                        if entry.name.isascii() and entry.name.isdecimal()
                    ),
                    key=int,
                )
            if final_fds != fds or any(os.readlink(fd_dir / fd) != fd_links[fd] for fd in fds):
                raise ValueError("namespace FD roster/target changed during census")
            end = _read_regular(pid_dir / "stat", 8192)
            if (
                end.split(" (", 1)[0] != name
                or _birth(end) != birth
                or os.readlink(pid_dir / "ns/mnt") != pid_mnt
                or os.readlink(pid_dir / "ns/net") != pid_net
            ):
                raise ValueError("PID or namespace changed during FD scan")
            digest.update(
                json.dumps(
                    [name, birth, pid_mnt, pid_net, holder_fd], separators=(",", ":")
                ).encode()
            )
            matches.extend(f"{name}:{birth}:{holder}" for holder in holder_fd)
            scanned += 1
        with os.scandir(proc_root) as iterator:
            final_candidates = sorted(
                (
                    entry.name
                    for entry in iterator
                    if entry.name.isascii() and entry.name.isdecimal()
                ),
                key=int,
            )
        if final_candidates != candidates:
            raise ValueError("visible namespace process roster changed during census")
        if original_identity() != (original_mnt, original_net):
            raise ValueError("original namespace changed during census")
        return _component(
            "PRESENT" if (matches or process_holders) else "NOT_OBSERVED",
            f"local_proc_pids={scanned}; matching_namespace_processes={len(process_holders)}; "
            f"matching_namespace_fds={len(matches)}; "
            f"inventory_sha256={digest.hexdigest()}; "
            "foreign PID namespaces, kernel references and hidden FD holders unverified",
        )
    except (OSError, ValueError, OverflowError, UnicodeError):
        return _component("UNKNOWN", "namespace holder census incomplete, raced, or over budget")


def _visible_foreign_mount_targets(scope: Scope, proc_root: Path) -> dict[str, str]:
    """Inspect targets in *visible* foreign mount namespaces, without effects.

    The original process is required to pin the namespace identity; once it is
    gone, a local /proc view cannot reconstruct every Run-owned namespace.
    A zero-match result is NOT physical absence, even if both scans agree.
    """
    try:
        tracked = proc_root / str(scope.tracked_pid)
        if proc_root.is_symlink() or tracked.is_symlink():
            raise OSError("proc root or original process substituted")
        boot = _read_regular(proc_root / "sys/kernel/random/boot_id", 128).strip()
        if boot != scope.tracked_birth.rpartition(":")[0]:
            raise ValueError("original boot identity changed")

        def tracked_identity() -> str:
            original = _read_regular(tracked / "stat", 8192)
            if original.split(" (", 1)[0] != str(scope.tracked_pid) or (
                f"{boot}:{_birth(original)}" != scope.tracked_birth
            ):
                raise ValueError("original PID/birth identity replaced")
            value = os.readlink(tracked / "ns/mnt")
            if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", value):
                raise ValueError("invalid original mount namespace")
            return value

        original_mnt = tracked_identity()
        with os.scandir(proc_root) as entries:
            pids = sorted(
                (
                    entry.name
                    for entry in entries
                    if entry.name.isascii() and entry.name.isdecimal()
                ),
                key=int,
            )
        if len(pids) > 512:
            raise ValueError("visible process census exceeds budget")
        namespace_fingerprints: dict[str, str] = {}
        digest = hashlib.sha256()
        matched: list[tuple[str, int, int]] = []
        total_mounts = 0
        targets = set(scope.mount_targets)
        for name in pids:
            directory = proc_root / name
            if directory.is_symlink() or not directory.is_dir():
                raise OSError("process directory inaccessible/substituted")
            stat_before = _read_regular(directory / "stat", 8192)
            if stat_before.split(" (", 1)[0] != name:
                raise ValueError("stat PID mismatch")
            birth = _birth(stat_before)
            namespace = os.readlink(directory / "ns/mnt")
            if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", namespace):
                raise ValueError("invalid process mount namespace")
            if namespace != original_mnt:
                # Read every visible holder and insist that holders claiming the
                # same namespace have an identical stable mount inventory.
                lines = _read_regular(directory / "mountinfo", 1_048_576).splitlines()
                if not lines or len(lines) > 8192 or total_mounts + len(lines) > 32768:
                    raise ValueError("empty or unbounded mount inventory")
                records: list[tuple[int, int, str]] = []
                mount_ids: set[int] = set()
                for line in lines:
                    fields = line.split()
                    if (
                        len(fields) < 10
                        or fields.count("-") != 1
                        or fields.index("-") < 6
                        or fields.index("-") + 3 > len(fields)
                        or not fields[0].isdecimal()
                        or not fields[1].isdecimal()
                    ):
                        raise ValueError("invalid foreign mountinfo record")
                    mount_id = int(fields[0])
                    if mount_id < 1 or mount_id in mount_ids:
                        raise ValueError("invalid or duplicate foreign mount ID")
                    mount_ids.add(mount_id)
                    records.append((mount_id, int(fields[1]), _mount_field(fields[4])))
                total_mounts += len(records)
                inventory = hashlib.sha256(
                    json.dumps(sorted(records), separators=(",", ":")).encode()
                ).hexdigest()
                if (
                    namespace in namespace_fingerprints
                    and namespace_fingerprints[namespace] != inventory
                ):
                    raise ValueError("shared namespace mount inventory inconsistent")
                namespace_fingerprints[namespace] = inventory
                matched.extend(
                    (name, int(birth), mid) for mid, _parent, target in records if target in targets
                )
                digest.update(
                    json.dumps([name, birth, namespace, inventory], separators=(",", ":")).encode()
                )
            stat_after = _read_regular(directory / "stat", 8192)
            if (
                stat_after.split(" (", 1)[0] != name
                or _birth(stat_after) != birth
                or os.readlink(directory / "ns/mnt") != namespace
            ):
                raise ValueError("PID/birth or namespace changed during mount scan")
        # CO-R3R: a foreign task may appear, vanish, or escape while we scan.
        # The final PID roster is part of the same scoped observation, not a
        # complete task census or an independent physical absence proof.
        with os.scandir(proc_root) as entries:
            final_pids = sorted(
                (
                    entry.name
                    for entry in entries
                    if entry.name.isascii() and entry.name.isdecimal()
                ),
                key=int,
            )
        if final_pids != pids:
            raise ValueError("visible foreign mount process roster changed during census")
        if tracked_identity() != original_mnt:
            raise ValueError("original namespace changed during inventory")
        return _component(
            "PRESENT" if matched else "NOT_OBSERVED",
            f"visible_pids={len(pids)}; foreign_namespaces={len(namespace_fingerprints)}; "
            f"foreign_mount_records={total_mounts}; matched_mount_targets={len(matched)}; "
            f"inventory_sha256={digest.hexdigest()}; "
            "hidden PID namespaces, detached mounts, and kernel-held references unverified",
        )
    except (OSError, ValueError, UnicodeError, OverflowError):
        return _component(
            "UNKNOWN", "visible foreign mount inventory incomplete, raced, or over budget"
        )


def _visible_mount_fd_holders(scope: Scope, proc_root: Path) -> dict[str, str]:
    """Observe *visible* FDs referencing pinned mount IDs in the original namespace.

    Linux fdinfo mnt_id is meaningful only with its mount namespace identity.
    This is a development-only hint: even exhaustive visible /proc does not
    account for detached mounts, hidden tasks, kernel references or FD races.
    It must never be interpreted as proof of physical absence.
    """
    try:
        if proc_root.is_symlink():
            raise OSError("proc root substituted")
        boot = _read_regular(proc_root / "sys/kernel/random/boot_id", 128).strip()
        if boot != scope.tracked_birth.rpartition(":")[0]:
            raise ValueError("different boot")
        original = proc_root / str(scope.tracked_pid)
        if original.is_symlink():
            raise OSError("original PID directory substituted")

        def original_identity() -> str:
            entry = _read_regular(original / "stat", 8192)
            if (
                entry.split(" (", 1)[0] != str(scope.tracked_pid)
                or f"{boot}:{_birth(entry)}" != scope.tracked_birth
            ):
                raise ValueError("original PID birth changed")
            handle = os.readlink(original / "ns/mnt")
            if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", handle):
                raise ValueError("invalid original namespace handle")
            return handle

        namespace = original_identity()
        raw = _read_regular(original / "mountinfo", 1_048_576).splitlines()
        if not raw or len(raw) > 8192:
            raise ValueError("unbounded original mount inventory")
        target_ids: set[int] = set()
        seen_ids: set[int] = set()
        # The B19 census must consume the same COMPLETE declared-target
        # coverage as the original LACE tracked-namespace observation. A
        # partial or stacked roster cannot support a mount/FD lifecycle hint.
        targets = set(scope.mount_targets)
        matched_targets: dict[str, list[tuple[int, str, str]]] = {}
        for line in raw:
            fields = line.split()
            if (
                len(fields) < 10
                or fields.count("-") != 1
                or fields.index("-") < 6
                or fields.index("-") + 3 > len(fields)
                or not fields[0].isdecimal()
                or not fields[1].isdecimal()
            ):
                raise ValueError("malformed original mountinfo")
            mount_id = int(fields[0])
            if mount_id < 1 or mount_id in seen_ids:
                raise ValueError("duplicate or invalid original mount ID")
            if not re.fullmatch(r"[0-9]+:[0-9]+", fields[2]):
                raise ValueError("invalid original mount device")
            seen_ids.add(mount_id)
            target = _mount_field(fields[4])
            if target in targets:
                matched_targets.setdefault(target, []).append(
                    (mount_id, fields[2], _mount_field(fields[3]))
                )
                target_ids.add(mount_id)
        if set(matched_targets) != targets or any(
            len(entries) != 1 for entries in matched_targets.values()
        ):
            return _component("UNKNOWN", "incomplete or stacked original target mount identities")
        target_fingerprint = hashlib.sha256(
            json.dumps(
                [(target, matched_targets[target][0]) for target in sorted(targets)],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if original_identity() != namespace:
            raise ValueError("original process/namespace changed")

        with os.scandir(proc_root) as iterator:
            pids = sorted(
                (item.name for item in iterator if item.name.isascii() and item.name.isdecimal()),
                key=int,
            )
        if len(pids) > 512:
            raise ValueError("visible process budget exceeded")
        digest = hashlib.sha256()
        inspected_fds = 0
        found = 0
        for pid in pids:
            directory = proc_root / pid
            if directory.is_symlink() or not directory.is_dir():
                raise OSError("visible PID directory substituted")
            first = _read_regular(directory / "stat", 8192)
            if first.split(" (", 1)[0] != pid:
                raise ValueError("visible PID stat mismatch")
            birth = _birth(first)
            observed_ns = os.readlink(directory / "ns/mnt")
            if not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", observed_ns):
                raise ValueError("malformed mount namespace")
            if observed_ns == namespace:
                fd_root = directory / "fd"
                info_root = directory / "fdinfo"
                if fd_root.is_symlink() or info_root.is_symlink():
                    raise OSError("FD directories substituted")
                with os.scandir(fd_root) as handles:
                    names = sorted(
                        (entry.name for entry in handles), key=lambda n: (not n.isdecimal(), n)
                    )
                if len(names) > 256 or any(
                    not name.isascii() or not name.isdecimal() for name in names
                ):
                    raise ValueError("invalid or over-budget FD census")

                # A closed/reused FD number can keep the same name while
                # changing mnt_id. Compare kernel mount identities twice,
                # never unstable offsets or fdinfo flags.
                def read_mount_id(fd: str, info_root: Path = info_root) -> int:
                    record = _read_regular(info_root / fd, 8192)
                    values = [
                        line.split(":", 1)[1].strip()
                        for line in record.splitlines()
                        if line.startswith("mnt_id:")
                    ]
                    if len(values) != 1 or not values[0].isdecimal() or int(values[0]) < 1:
                        raise ValueError("fdinfo missing or malformed mnt_id")
                    return int(values[0])

                mount_ids = {fd: read_mount_id(fd) for fd in names}
                for fd, mount_id in mount_ids.items():
                    found += mount_id in target_ids
                    inspected_fds += 1
                    digest.update(
                        json.dumps([pid, birth, fd, mount_id], separators=(",", ":")).encode()
                    )
                # A vanished or newly published descriptor during the census
                # must invalidate the sample, including apparent zero holders.
                with os.scandir(fd_root) as handles:
                    after_names = sorted(
                        (entry.name for entry in handles), key=lambda n: (not n.isdecimal(), n)
                    )
                if after_names != names or any(read_mount_id(fd) != mount_ids[fd] for fd in names):
                    raise ValueError("FD roster or mount ID changed during census")
            after = _read_regular(directory / "stat", 8192)
            if (
                after.split(" (", 1)[0] != pid
                or _birth(after) != birth
                or os.readlink(directory / "ns/mnt") != observed_ns
            ):
                raise ValueError("visible PID or namespace changed")
        with os.scandir(proc_root) as iterator:
            final_pids = sorted(
                (item.name for item in iterator if item.name.isascii() and item.name.isdecimal()),
                key=int,
            )
        if final_pids != pids:
            raise ValueError("visible mount FD process roster changed during census")
        if original_identity() != namespace:
            raise ValueError("original PID or namespace changed after census")
        if _read_regular(original / "mountinfo", 1_048_576).splitlines() != raw:
            raise ValueError("original mount identity changed during FD census")
        return _component(
            "PRESENT" if found else "NOT_OBSERVED",
            f"visible_pids={len(pids)}; original_namespace={namespace}; "
            f"target_mount_ids={len(target_ids)}; "
            f"target_kernel_sha256={target_fingerprint}; inspected_fds={inspected_fds}; "
            f"matching_mount_fds={found}; inventory_sha256={digest.hexdigest()}; "
            "hidden tasks, detached mounts, and kernel-held references unverified",
        )
    except (OSError, ValueError, UnicodeError, OverflowError):
        return _component("UNKNOWN", "visible mount FD census incomplete, raced, or over budget")


def _correlate_original_mount_fd_snapshot(
    facts: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Fail closed when TWO original read-only probes disagree on kernel identity.

    Both reports are from the existing shared Linux observer. This does not
    establish actual bwrap FD consumption, trusted /proc completeness, or
    physical absence; it only prevents independently stable but inconsistent
    target-mount samples from being treated as mutually corroborating facts.
    """
    tracked = facts["tracked_mount_namespace"]
    holders = facts["visible_mount_fd_holders"]
    if not (
        "target_kernel_sha256=" in tracked["detail"] or "target_kernel_sha256=" in holders["detail"]
    ):
        return facts  # No usable kernel correlation on unavailable proc reads.
    if tracked["state"] == "UNKNOWN" or holders["state"] == "UNKNOWN":
        return facts  # Preserve separately scoped facts; no correspondence asserted.

    def identity(detail: str, *, namespace_field: str) -> tuple[str, str] | None:
        namespace = re.search(
            r"(?:^|; )" + namespace_field + r"=(mnt:\[[1-9][0-9]*\])(?:;|$)", detail
        )
        fingerprint = re.search(r"(?:^|; )target_kernel_sha256=([0-9a-f]{64})(?:;|$)", detail)
        if namespace is None or fingerprint is None:
            return None
        return namespace.group(1), fingerprint.group(1)

    tracked_id = identity(tracked["detail"], namespace_field="tracked process mount namespace")
    fd_id = identity(holders["detail"], namespace_field="original_namespace")
    # The source of mount truth for FD mnt_id must be the same complete target
    # roster and namespace as the tracked process, not another PID's mount IDs.
    if (
        tracked["state"] != "PRESENT"
        or holders["state"] not in ("PRESENT", "NOT_OBSERVED")
        or tracked_id is None
        or fd_id is None
        or tracked_id != fd_id
    ):
        facts["tracked_mount_namespace"] = _component(
            "UNKNOWN",
            "original mount and FD probes do not agree on complete kernel target identity",
        )
        facts["visible_mount_fd_holders"] = _component(
            "UNKNOWN",
            "original mount and FD probes do not agree on complete kernel target identity",
        )
    return facts


PROBES = (
    "_cgroup",
    "_cgroup_subtree",
    "_process",
    "_mounts",
    "_network",
    "_tracked_namespace_inventory",
    "_namespace_fd_holders",
    "_visible_foreign_mount_targets",
    "_visible_mount_fd_holders",
)


def observe_kernel(
    scope: Scope,
    *,
    proc_root: Path = Path("/proc"),
    diagnostic_probes: Mapping[str, Callable[..., object]] | None = None,
) -> PhysicalKernelSnapshot:
    """Double-scan the same kernel mechanism; never publish a verdict.

    Diagnostic injection preserves old adversarial monkeypatch tests; it is
    NOT a production-authority hook. The original LACE owner must never
    derive admission or signed facts from a caller-selected Scope.
    """
    methods = {name: globals()[name] for name in PROBES}
    if diagnostic_probes is not None:
        if set(diagnostic_probes) != set(PROBES):
            raise ValueError("exact diagnostic probe set required")
        methods = dict(diagnostic_probes)

    def scan() -> dict[str, dict[str, str]]:
        return {
            "cgroup": methods["_cgroup"](scope),
            "subtree": methods["_cgroup_subtree"](scope, proc_root),
            "process": methods["_process"](scope, proc_root),
            "mounts": methods["_mounts"](scope, proc_root),
            "network": methods["_network"](scope),
            **methods["_tracked_namespace_inventory"](scope, proc_root),
            "visible_namespace_fd_holders": methods["_namespace_fd_holders"](scope, proc_root),
            "visible_foreign_mount_targets": methods["_visible_foreign_mount_targets"](
                scope, proc_root
            ),
            "visible_mount_fd_holders": methods["_visible_mount_fd_holders"](scope, proc_root),
        }

    first, second = scan(), scan()
    if first.keys() != second.keys():
        raise ValueError("physical component set changed during observation")
    combined = {
        name: (
            second[name]
            if first[name] == second[name]
            else _component("UNKNOWN", "observation changed during read")
        )
        for name in first
    }
    # The port owns the exact diagnostic component roster; reject additions,
    # deletions and reordering instead of silently letting a reduced scan
    # masquerade as complete post-cleanup observation.
    if tuple(first) != ORIGINAL_KERNEL_COMPONENTS:
        raise ValueError("original physical observer component roster changed")
    # Exactly one shared observer: correlate AFTER the original double scan,
    # before any non-authoritative snapshot projection. Never infer absence.
    combined = _correlate_original_mount_fd_snapshot(combined)
    facts = []
    for name in first:
        value = combined[name]
        if (
            type(value) is not dict
            or set(value) != {"state", "detail"}
            or value["state"] not in ("PRESENT", "NOT_OBSERVED", "UNKNOWN")
            or type(value["detail"]) is not str
        ):
            raise ValueError("invalid physical kernel fact")
        facts.append(
            (
                name,
                PhysicalKernelFact(
                    cast(Literal["PRESENT", "NOT_OBSERVED", "UNKNOWN"], value["state"]),
                    value["detail"],
                ),
            )
        )
    return PhysicalKernelSnapshot(tuple(facts))
