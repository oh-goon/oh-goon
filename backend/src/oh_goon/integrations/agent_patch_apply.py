"""Canonical bounded proposal validation, consumed by original Step dataflow.

This owner interprets untrusted proposal data only. A later explicitly admitted
command Step decides whether to launch the native apply CLI; this module does
not turn an Agent's output into write authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import RepositoryPathSpace, repository_relative_parts
from oh_goon.core.source_tree_policy import is_transient_source_path
from oh_goon.integrations.exact_file_write import (
    exact_file_revision,
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
)

_MAX_PROPOSAL_BYTES = 4096
_FIELDS = {"schema", "handoff_identity", "path", "source_sha256", "replacement", "summary"}


def add_apply_arguments(parser: argparse.ArgumentParser) -> None:
    """The original apply CLI's argument contract, also checked before repair admission."""
    parser.add_argument("--proposal", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--expected-path")
    selection.add_argument("--write-root", action="append", dest="write_roots")
    parser.add_argument("--expected-candidate-identity")
    parser.add_argument("--expected-handoff-identity")


def parse_apply_arguments(arguments: tuple[str, ...]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False, allow_abbrev=False)
    add_apply_arguments(parser)
    try:
        # argparse's error path raises SystemExit for missing required options.
        return parser.parse_args(arguments)
    except (argparse.ArgumentError, SystemExit) as exc:
        raise FleetPlanInvalid("repair apply Step has invalid native apply arguments") from exc


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise FleetPlanInvalid("duplicate proposal property")
        output[key] = value
    return output


def parse_proposal_receipt(text: str, *, handoff_identity: str | None = None) -> dict[str, str]:
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_PROPOSAL_BYTES:
        raise FleetPlanInvalid("proposal is not bounded text")
    try:
        data = json.loads(text, object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError) as exc:
        raise FleetPlanInvalid("proposal must be one JSON object") from exc
    if type(data) is not dict or set(data) != _FIELDS:
        raise FleetPlanInvalid("proposal receipt has invalid fields")
    if data["schema"] != "agent-patch-proposal-v1":
        raise FleetPlanInvalid("unsupported proposal schema")
    path, digest, identity = data["path"], data["source_sha256"], data["handoff_identity"]
    if (
        type(identity) is not str
        or not identity
        or len(identity) > 200
        or (handoff_identity is not None and identity != handoff_identity)
    ):
        raise FleetPlanInvalid("proposal is not bound to selected source handoff")
    if (
        type(path) is not str
        or not path.startswith("repo/")
        or len(path) > 200
        or type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise FleetPlanInvalid("proposal has invalid source path or revision")
    repository_relative_parts(path[5:], label="agent patch target")
    replacement, summary = data["replacement"], data["summary"]
    if (
        type(replacement) is not str
        or not replacement
        or len(replacement.encode("utf-8")) > 2048
        or "\x00" in replacement
        or type(summary) is not str
        or not summary.strip()
        or len(summary) > 500
        or any(ord(c) < 32 or ord(c) == 127 for c in summary)
    ):
        raise FleetPlanInvalid("proposal content or summary is invalid")
    return data


def proposal_from_step_stdout(stdout: str, *, handoff_identity: str) -> str:
    """Extract only original validated proposal receipt from the Step stdout."""
    if type(stdout) is not str or not stdout.endswith("\n"):
        raise FleetPlanInvalid("successful proposal Step lacks a terminal receipt")
    marker = "Agent proposal only; no repository mutation or tests executed\n"
    if marker not in stdout:
        raise FleetPlanInvalid("successful proposal Step lacks proposal boundary")
    candidate = stdout.rstrip("\n").split("\n")[-1]
    value = parse_proposal_receipt(candidate, handoff_identity=handoff_identity)
    # Freeze one canonical scalar for existing step-output binding.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def apply_selected_proposal(
    proposal_text: str,
    *,
    workspace: Path,
    expected_path: str,
    expected_handoff_identity: str | None = None,
) -> tuple[str, str]:
    """Exact, cooperating locked CAS on an explicitly selected workspace file.

    Caller (an admitted ordinary write Step) owns write authorization. No
    repository mutation is triggered by proposal publication itself.
    """
    proposal = parse_proposal_receipt(proposal_text, handoff_identity=expected_handoff_identity)
    if type(expected_path) is not str or expected_path != proposal["path"][5:]:
        raise FleetPlanInvalid("apply Step did not select the proposed exact path")
    root = RepositoryPathSpace.from_root(workspace)
    target = root.resolve(
        expected_path, label="Agent apply target", kind="file", require_exists=True
    )
    # Containment is not enough: do not silently follow any repository-local
    # symlink or alias when a proposal is for one original exact member.
    current = root.root
    for part in repository_relative_parts(expected_path, label="Agent apply target").parts:
        current = current / part
        if current.is_symlink():
            raise FleetPlanInvalid("Agent apply target cannot traverse symlinks")
    with exact_file_write_lock(target):
        if not target.is_file() or exact_file_revision(target) != proposal["source_sha256"]:
            raise FleetPlanInvalid("Agent proposal source revision is stale; no file changed")
        try:
            original = target.read_bytes()
        except OSError as exc:
            raise FleetPlanInvalid("cannot read exact Agent apply target") from exc
        if len(original) > 2048 or original == proposal["replacement"].encode("utf-8"):
            raise FleetPlanInvalid("Agent apply source is invalid or already matches proposal")
        with prepared_text_file(target, proposal["replacement"], newline="") as prepared:
            replace_prepared_file(prepared, target)
    return expected_path, hashlib.sha256(proposal["replacement"].encode("utf-8")).hexdigest()


MAX_BATCH_BYTES = 65536


def parse_batch_proposal(text: str, *, candidate_identity: str) -> dict[str, Any]:
    """A proposal is source-bound data; allowed write roots come only from the caller."""
    if (
        not isinstance(candidate_identity, str)
        or not candidate_identity
        or len(candidate_identity) > 160
    ):
        raise FleetPlanInvalid("batch apply requires an explicit candidate identity")
    if type(text) is not str or len(text.encode("utf-8")) > MAX_BATCH_BYTES:
        raise FleetPlanInvalid("batch proposal exceeds its byte budget")
    try:
        data = json.loads(text, object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError) as exc:
        raise FleetPlanInvalid("batch proposal must be JSON") from exc
    if (
        type(data) is not dict
        or set(data)
        not in (
            {"schema", "candidate_identity", "operations", "summary"},
            {"schema", "candidate_identity", "operations", "summary", "regressions"},
        )
        or data["schema"] != "agent-patch-batch-v1"
        or data["candidate_identity"] != candidate_identity
        or not isinstance(data["summary"], str)
        or not data["summary"].strip()
        or len(data["summary"]) > 4000
        or type(data["operations"]) is not list
        or not 1 <= len(data["operations"]) <= 32
    ):
        raise FleetPlanInvalid("batch proposal has an invalid source-bound schema")
    paths = set()
    for operation in data["operations"]:
        if type(operation) is not dict or set(operation) != {
            "action",
            "path",
            "source_sha256",
            "replacement",
        }:
            raise FleetPlanInvalid("batch operation has invalid fields")
        path = operation["path"]
        if not isinstance(path, str) or not path.startswith("repo/") or len(path) > 240:
            raise FleetPlanInvalid("batch operation requires an exact repo/ path")
        parts = repository_relative_parts(path[5:], label="batch target").parts
        if not parts or is_transient_source_path(path[5:]) or path in paths:
            raise FleetPlanInvalid(
                "batch targets must be distinct source paths outside runtime state"
            )
        paths.add(path)
        action, digest, replacement = (
            operation["action"],
            operation["source_sha256"],
            operation["replacement"],
        )
        if action not in {"create", "update", "delete"}:
            raise FleetPlanInvalid("unsupported batch action")
        if (action == "create" and digest is not None) or (
            action != "create"
            and (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None)
        ):
            raise FleetPlanInvalid("batch operation has invalid preimage")
        if (action == "delete" and replacement is not None) or (
            action != "delete"
            and (
                not isinstance(replacement, str)
                or "\x00" in replacement
                or len(replacement.encode("utf-8")) > 32768
            )
        ):
            raise FleetPlanInvalid("batch replacement exceeds its text budget")
    return data


def validate_write_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    if not roots or len(roots) > 32 or len(set(roots)) != len(roots):
        raise FleetPlanInvalid("repair requires explicit distinct write roots")
    for root in roots:
        parts = repository_relative_parts(root, label="repair write root").parts
        if not parts or is_transient_source_path(root):
            raise FleetPlanInvalid(
                "repair write roots must select source paths outside runtime state"
            )
    return roots


@dataclass(frozen=True, slots=True)
class BatchApplyReceipt:
    candidate_identity: str
    affected_paths: tuple[str, ...]
    complete: bool


class BatchApplyIncomplete(FleetPlanInvalid):
    def __init__(self, affected_paths: tuple[str, ...]):
        self.affected_paths = affected_paths
        super().__init__(
            f"batch apply INCOMPLETE; possibly changed paths: {', '.join(affected_paths)}"
        )


def apply_batch_proposal(
    text: str, *, workspace: Path, candidate_identity: str, write_roots: tuple[str, ...]
) -> BatchApplyReceipt:
    """Preflight the entire batch, then publish via native directory-relative CAS.

    A filesystem cannot atomically commit multiple paths. Failure after the first
    publication reports the affected prefix and never becomes successful evidence.
    """
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise FleetPlanInvalid(
            "batch apply requires native directory-relative no-follow file operations"
        )
    proposal = parse_batch_proposal(text, candidate_identity=candidate_identity)
    roots = validate_write_roots(write_roots)
    operations = proposal["operations"]
    for operation in operations:
        relative = operation["path"][5:]
        if not any(relative == root or relative.startswith(root + "/") for root in roots):
            raise FleetPlanInvalid("batch target is outside the caller's write roots")
    affected = []
    with ExitStack() as stack:
        root_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root_fd)
        prepared = []
        directories = []
        for operation in sorted(operations, key=lambda item: item["path"]):
            relative = operation["path"][5:]
            stack.enter_context(exact_file_write_lock(workspace / relative))
            parent_fd = root_fd
            parts = relative.split("/")
            for part in parts[:-1]:
                child_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                )
                stack.callback(os.close, child_fd)
                directories.append((parent_fd, part, child_fd))
                parent_fd = child_fd
            name = parts[-1]
            try:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd
                )
            except FileNotFoundError:
                descriptor = None
            source = None
            state = None
            mode = 0o644
            if descriptor is not None:
                with os.fdopen(descriptor, "rb") as stream:
                    state = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(state.st_mode)
                        or state.st_nlink != 1
                        or state.st_size > 8 * 1024 * 1024
                    ):
                        raise FleetPlanInvalid(
                            "batch preimage must be a bounded regular source file"
                        )
                    source = stream.read(8 * 1024 * 1024 + 1)
                    if _batch_stat_identity(os.fstat(stream.fileno())) != _batch_stat_identity(
                        state
                    ):
                        raise FleetPlanInvalid(
                            "batch source changed during preflight; no file changed"
                        )
                    mode = stat.S_IMODE(state.st_mode)
            digest = hashlib.sha256(source).hexdigest() if source is not None else None
            if digest != operation["source_sha256"]:
                raise FleetPlanInvalid("batch preimage is stale; no file changed")
            if operation["action"] != "delete":
                replacement = operation["replacement"].encode("utf-8")
                if replacement == source:
                    raise FleetPlanInvalid("batch contains an unchanged operation")
                temporary = ".oh-goon-batch-" + secrets.token_hex(16)
                fd = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=parent_fd
                )
                stack.callback(_remove_batch_temporary, parent_fd, temporary)
                with os.fdopen(fd, "wb") as stream:
                    if source is not None:
                        os.fchmod(stream.fileno(), mode)
                    stream.write(replacement)
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                temporary = None
            prepared.append((operation, parent_fd, name, temporary, _batch_stat_identity(state)))
        # Validate all prepared preimages before publishing the first member.
        _require_batch_directories(workspace, root_fd, directories)
        for _, parent_fd, name, _, expected in prepared:
            _require_batch_preimage(parent_fd, name, expected)
        try:
            for operation, parent_fd, name, temporary, expected in prepared:
                _require_batch_directories(workspace, root_fd, directories)
                _require_batch_preimage(parent_fd, name, expected)
                affected.append(operation["path"][5:])
                if temporary is None:
                    os.unlink(name, dir_fd=parent_fd)
                elif operation["action"] == "create":
                    # Native exclusive publication never overwrites a concurrent create.
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    os.unlink(temporary, dir_fd=parent_fd)
                else:
                    os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            _require_batch_directories(workspace, root_fd, directories)
        except (OSError, FleetPlanInvalid) as exc:
            if not affected:
                raise
            raise BatchApplyIncomplete(tuple(affected)) from exc
    return BatchApplyReceipt(candidate_identity, tuple(affected), True)


def _require_batch_directories(
    workspace: Path, root_fd: int, directories: list[tuple[int, str, int]]
) -> None:
    """An open directory must still belong to its original workspace namespace."""

    def require_same_directory(actual: os.stat_result, descriptor: int) -> None:
        pinned = os.fstat(descriptor)
        if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
            pinned.st_dev,
            pinned.st_ino,
        ):
            raise FleetPlanInvalid("batch directory changed after preflight")

    require_same_directory(os.stat(workspace, follow_symlinks=False), root_fd)
    for parent_fd, name, child_fd in directories:
        require_same_directory(os.stat(name, dir_fd=parent_fd, follow_symlinks=False), child_fd)


def _exists_at(parent: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _remove_batch_temporary(parent: int, name: str) -> None:
    active = sys.exception()
    try:
        if _exists_at(parent, name):
            os.unlink(name, dir_fd=parent)
    except OSError as error:
        if active is not None and active is not error:
            active.add_note(f"batch temporary cleanup failed: {error}")
        else:
            raise


def _batch_stat_identity(state: os.stat_result | None) -> tuple[int, ...] | None:
    if state is None:
        return None
    return (
        state.st_dev,
        state.st_ino,
        state.st_mode,
        state.st_size,
        state.st_nlink,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def _require_batch_preimage(parent: int, name: str, expected: tuple[int, ...] | None) -> None:
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    if _batch_stat_identity(current) != expected:
        raise FleetPlanInvalid("batch preimage changed after preflight")
