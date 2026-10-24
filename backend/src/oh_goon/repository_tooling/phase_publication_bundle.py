"""Stage, verify, and commit immutable phase-publication evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

SCHEMA = "oh-goon.phase-publication/v1"
COMMIT_SCHEMA = "oh-goon.phase-publication-commit/v1"
ROLES = ("candidate", "parent_patch", "handoff", "checksums")


class ArtifactMetadata(TypedDict):
    name: str
    sha256: str
    bytes: int


class PublicationManifest(TypedDict):
    schema: str
    phase: str
    parent: str
    state: str
    artifacts: dict[str, ArtifactMetadata]


class PublicationCommit(PublicationManifest):
    manifest: ArtifactMetadata


class PublicationDiscovery(TypedDict):
    phase: str
    parent: str
    directory: str
    commit: str


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_bytes(data: Mapping[str, object]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build(directory: Path, phase: str, parent: str, names: dict[str, str]) -> PublicationManifest:
    artifacts: dict[str, ArtifactMetadata] = {}
    for role in ROLES:
        path = directory / names[role]
        if not path.is_file():
            raise ValueError(f"missing required publication artifact: {role}: {path.name}")
        artifacts[role] = {
            "name": path.name,
            "sha256": digest(path),
            "bytes": path.stat().st_size,
        }
    return {
        "schema": SCHEMA,
        "phase": phase,
        "parent": parent,
        "state": "staged",
        "artifacts": artifacts,
    }


def _artifact_issues(directory: Path, role: str, meta: object) -> list[str]:
    if not isinstance(meta, dict):
        return []
    path = directory / meta.get("name", "")
    if not path.is_file():
        return [f"missing persisted artifact: {role}"]
    issues: list[str] = []
    if path.stat().st_size != meta.get("bytes"):
        issues.append(f"byte-size mismatch: {role}")
    if digest(path) != meta.get("sha256"):
        issues.append(f"sha256 mismatch: {role}")
    return issues


def verify(directory: Path, manifest: PublicationManifest) -> list[str]:
    issues: list[str] = []
    checks = (
        (manifest.get("schema") != SCHEMA, "unsupported schema"),
        (manifest.get("state") != "staged", "manifest is not staged"),
        (not manifest.get("phase") or not manifest.get("parent"), "phase and parent are required"),
    )
    issues.extend(message for failed, message in checks if failed)
    artifacts = manifest.get("artifacts", {})
    if set(artifacts) != set(ROLES):
        issues.append("artifact role set is incomplete or contains unknown roles")
    for role in ROLES:
        issues.extend(_artifact_issues(directory, role, artifacts.get(role)))
    return issues


def build_commit(
    directory: Path, manifest_path: Path, manifest: PublicationManifest
) -> PublicationCommit:
    issues = verify(directory, manifest)
    if issues:
        raise ValueError("; ".join(issues))
    manifest_bytes = manifest_path.read_bytes()
    if json.loads(manifest_bytes) != manifest:
        raise ValueError("manifest bytes do not decode to verified manifest")
    return {
        "schema": COMMIT_SCHEMA,
        "state": "committed",
        "phase": manifest["phase"],
        "parent": manifest["parent"],
        "manifest": {
            "name": manifest_path.name,
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "bytes": len(manifest_bytes),
        },
        "artifacts": manifest["artifacts"],
    }


def _load_bound_manifest(
    directory: Path, meta: object
) -> tuple[PublicationManifest | None, list[str]]:
    if not isinstance(meta, dict):
        return None, ["commit manifest binding is missing"]
    path = directory / meta.get("name", "")
    if not path.is_file():
        return None, ["committed manifest is missing"]
    raw = path.read_bytes()
    issues: list[str] = []
    if len(raw) != meta.get("bytes"):
        issues.append("committed manifest byte-size mismatch")
    if hashlib.sha256(raw).hexdigest() != meta.get("sha256"):
        issues.append("committed manifest sha256 mismatch")
    try:
        return json.loads(raw), issues
    except json.JSONDecodeError:
        return None, issues + ["committed manifest is invalid JSON"]


def verify_commit(directory: Path, commit: PublicationCommit) -> list[str]:
    issues: list[str] = []
    checks = (
        (commit.get("schema") != COMMIT_SCHEMA, "unsupported commit schema"),
        (commit.get("state") != "committed", "publication is not committed"),
    )
    issues.extend(message for failed, message in checks if failed)
    manifest, manifest_issues = _load_bound_manifest(directory, commit.get("manifest"))
    issues.extend(manifest_issues)
    if manifest is None:
        return issues
    issues.extend(verify(directory, manifest))
    bindings = (("phase", "phase"), ("parent", "parent"), ("artifacts", "artifact binding"))
    for key, label in bindings:
        if commit.get(key) != manifest.get(key):
            issues.append(f"commit {label} does not match manifest")
    return issues


def version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise ValueError(f"invalid numeric phase version: {value}") from exc


def _committed_candidate(directory: Path) -> tuple[str, PublicationCommit] | None:
    commits = sorted(directory.glob("OH-GOON-*-AUTHORITY-COMMIT.json"))
    if not commits:
        return None
    if len(commits) != 1:
        raise ValueError(f"ambiguous authority commit records: {directory.name}")
    try:
        record = json.loads(commits[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    phase = record.get("phase")
    if verify_commit(directory, record) or not isinstance(phase, str):
        return None
    return phase, record


def discover(archive: Path) -> PublicationDiscovery:
    if not archive.is_dir():
        raise ValueError(f"archive directory does not exist: {archive}")
    valid: list[tuple[tuple[int, ...], Path, PublicationCommit]] = []
    seen: dict[str, Path] = {}
    for directory in sorted(path for path in archive.iterdir() if path.is_dir()):
        candidate = _committed_candidate(directory)
        if candidate is None:
            continue
        phase, record = candidate
        if phase in seen:
            raise ValueError(
                f"ambiguous committed authority for phase {phase}: {seen[phase].name}, {directory.name}"
            )
        seen[phase] = directory
        valid.append((version_key(phase), directory, record))
    if not valid:
        raise ValueError("no valid committed development authority found")
    _, directory, record = max(valid, key=lambda item: item[0])
    return {
        "phase": record["phase"],
        "parent": record["parent"],
        "directory": directory.name,
        "commit": next(directory.glob("OH-GOON-*-AUTHORITY-COMMIT.json")).name,
    }


def write_create_only(path: Path, data: Mapping[str, object]) -> None:
    payload = canonical_bytes(data)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _command_create(args: argparse.Namespace) -> int:
    try:
        data = build(
            args.directory, args.phase, args.parent, {role: getattr(args, role) for role in ROLES}
        )
    except ValueError as exc:
        print(f"PHASE PUBLICATION: FAIL\n- {exc}")
        return 1
    print(canonical_bytes(data).decode("utf-8"), end="")
    return 0


def _print_issues(title: str, issues: list[str]) -> int:
    if not issues:
        return 0
    print(title)
    for issue in issues:
        print(f"- {issue}")
    return 1


def _command_verify(args: argparse.Namespace) -> int:
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    if _print_issues("PHASE PUBLICATION: FAIL", verify(args.directory, data)):
        return 1
    print(f"PHASE PUBLICATION: STAGED PASS ({data['phase']}, {len(ROLES)} artifacts)")
    return 0


def _command_commit(args: argparse.Namespace) -> int:
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        record = build_commit(args.directory, args.manifest, data)
        write_create_only(args.commit_record, record)
    except (ValueError, FileExistsError) as exc:
        print(f"PHASE PUBLICATION COMMIT: FAIL\n- {exc}")
        return 1
    print(f"PHASE PUBLICATION COMMIT: PASS ({record['parent']} -> {record['phase']})")
    return 0


def _command_discover(args: argparse.Namespace) -> int:
    try:
        result = discover(args.archive)
    except ValueError as exc:
        print(f"PHASE AUTHORITY DISCOVERY: FAIL\n- {exc}")
        return 1
    print(canonical_bytes(result).decode("utf-8"), end="")
    return 0


def _command_verify_commit(args: argparse.Namespace) -> int:
    record = json.loads(args.commit_record.read_text(encoding="utf-8"))
    if _print_issues("PHASE PUBLICATION COMMIT: FAIL", verify_commit(args.directory, record)):
        return 1
    print(f"PHASE PUBLICATION COMMIT: PASS ({record['parent']} -> {record['phase']})")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create")
    create.add_argument("directory", type=Path)
    create.add_argument("--phase", required=True)
    create.add_argument("--parent", required=True)
    for role in ROLES:
        create.add_argument(f"--{role.replace('_', '-')}", dest=role, required=True)
    check = sub.add_parser("verify")
    check.add_argument("directory", type=Path)
    check.add_argument("manifest", type=Path)
    commit = sub.add_parser("commit")
    commit.add_argument("directory", type=Path)
    commit.add_argument("manifest", type=Path)
    commit.add_argument("commit_record", type=Path)
    committed = sub.add_parser("verify-commit")
    committed.add_argument("directory", type=Path)
    committed.add_argument("commit_record", type=Path)
    discovery = sub.add_parser("discover")
    discovery.add_argument("archive", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    commands = {
        "create": _command_create,
        "verify": _command_verify,
        "commit": _command_commit,
        "verify-commit": _command_verify_commit,
        "discover": _command_discover,
    }
    return commands[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
