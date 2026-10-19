from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from oh_goon.core.file_digest import update_digest_from_file
from oh_goon.repository_tooling.repository_understanding_material import CANDIDATE_GOLD_MARKER


def _submission_sha256(path: Path) -> str:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"candidate submission does not exist: {path}")
    if path.is_symlink():
        raise ValueError("candidate submission must not be a symlink")
    digest = hashlib.sha256()
    if path.is_file():
        update_digest_from_file(digest, path)
        return digest.hexdigest()
    if not path.is_dir():
        raise ValueError("candidate submission must be a regular file or directory")
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            raise ValueError(f"candidate submission must not contain symlinks: {relative}")
        if not item.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        update_digest_from_file(digest, item)
        digest.update(b"\0")
    return digest.hexdigest()


def _submission_bundle(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    digest = _submission_sha256(path)
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    rows: list[dict[str, Any]] = []
    for item in files:
        relative = item.name if path.is_file() else item.relative_to(path).as_posix()
        body = item.read_bytes()
        row: dict[str, Any] = {"path": relative, "sha256": hashlib.sha256(body).hexdigest()}
        try:
            row["text"] = body.decode("utf-8")
            row["binary"] = False
        except UnicodeDecodeError:
            row["text"] = None
            row["binary"] = True
        rows.append(row)
    return {"sha256": digest, "files": rows}


def _validate_candidate_submission(path: Path) -> tuple[str, dict[str, Any]]:
    bundle = _submission_bundle(path)
    for item in bundle["files"]:
        text = item["text"]
        if isinstance(text, str) and CANDIDATE_GOLD_MARKER in text:
            raise ValueError(
                f"candidate submission references grader-only answer material: {item['path']}"
            )
    return bundle["sha256"], bundle


def _candidate_configuration_bundle(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("candidate configuration must be a regular JSON file")
    raw = path.read_bytes()
    try:
        configuration = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("candidate configuration must contain valid JSON") from exc
    if not isinstance(configuration, dict) or not configuration:
        raise ValueError("candidate configuration must be a non-empty JSON object")
    return {
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "configuration": configuration,
    }
