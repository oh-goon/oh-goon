"""Canonical bounded proposal validation, consumed by original Step dataflow.

This owner interprets untrusted proposal data only. A later explicitly admitted
command Step decides whether to launch the native apply CLI; this module does
not turn an Agent's output into write authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import RepositoryPathSpace, repository_relative_parts
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock, exact_file_revision,
    prepared_text_file, replace_prepared_file,
)

_MAX_PROPOSAL_BYTES = 4096
_FIELDS = {"schema", "handoff_identity", "path", "source_sha256", "replacement", "summary"}


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
    if (type(identity) is not str or not identity or len(identity) > 200
            or (handoff_identity is not None and identity != handoff_identity)):
        raise FleetPlanInvalid("proposal is not bound to selected source handoff")
    if (type(path) is not str or not path.startswith("repo/") or len(path) > 200
            or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise FleetPlanInvalid("proposal has invalid source path or revision")
    repository_relative_parts(path[5:], label="agent patch target")
    replacement, summary = data["replacement"], data["summary"]
    if (type(replacement) is not str or not replacement
            or len(replacement.encode("utf-8")) > 2048 or "\x00" in replacement
            or type(summary) is not str or not summary.strip() or len(summary) > 500
            or any(ord(c) < 32 or ord(c) == 127 for c in summary)):
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
    proposal_text: str, *, workspace: Path, expected_path: str,
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
    target = root.resolve(expected_path, label="Agent apply target", kind="file", require_exists=True)
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
