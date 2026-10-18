from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "backend/src/oh_goon/repository_tooling/phase_publication_bundle.py"


def _bundle(tmp_path: Path):
    names = {
        "candidate": "candidate.zip",
        "parent_patch": "parent.patch",
        "handoff": "handoff.md",
        "checksums": "checksums.txt",
    }
    for role, name in names.items():
        (tmp_path / name).write_bytes(role.encode())
    return names


def _manifest(tmp_path: Path) -> Path:
    names = _bundle(tmp_path)
    cmd = [sys.executable, str(SCRIPT), "create", str(tmp_path), "--phase", "9", "--parent", "8"]
    for role, name in names.items():
        cmd += ["--" + role.replace("_", "-"), name]
    made = subprocess.run(cmd, text=True, capture_output=True, check=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(made.stdout)
    return manifest


def test_complete_bundle_is_staged_but_not_committed(tmp_path: Path):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    assert data["state"] == "staged"
    assert not (tmp_path / "commit.json").exists()
    ok = subprocess.run([sys.executable, str(SCRIPT), "verify", str(tmp_path), str(manifest)], text=True, capture_output=True)
    assert ok.returncode == 0
    assert "STAGED PASS" in ok.stdout


def test_commit_is_create_only_and_binds_verified_manifest(tmp_path: Path):
    manifest = _manifest(tmp_path)
    commit = tmp_path / "commit.json"
    made = subprocess.run([sys.executable, str(SCRIPT), "commit", str(tmp_path), str(manifest), str(commit)], text=True, capture_output=True)
    assert made.returncode == 0
    record = json.loads(commit.read_text())
    assert record["state"] == "committed"
    assert record["parent"] == "8" and record["phase"] == "9"
    ok = subprocess.run([sys.executable, str(SCRIPT), "verify-commit", str(tmp_path), str(commit)], text=True, capture_output=True)
    assert ok.returncode == 0
    again = subprocess.run([sys.executable, str(SCRIPT), "commit", str(tmp_path), str(manifest), str(commit)], text=True, capture_output=True)
    assert again.returncode == 1


def test_mutation_before_commit_cannot_advance_authority(tmp_path: Path):
    manifest = _manifest(tmp_path)
    (tmp_path / "handoff.md").write_text("changed")
    commit = tmp_path / "commit.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "commit", str(tmp_path), str(manifest), str(commit)], text=True, capture_output=True)
    assert result.returncode == 1
    assert "handoff" in result.stdout
    assert not commit.exists()


def test_mutation_after_commit_invalidates_committed_authority(tmp_path: Path):
    manifest = _manifest(tmp_path)
    commit = tmp_path / "commit.json"
    subprocess.run([sys.executable, str(SCRIPT), "commit", str(tmp_path), str(manifest), str(commit)], check=True)
    manifest.write_text(manifest.read_text() + "\n")
    bad = subprocess.run([sys.executable, str(SCRIPT), "verify-commit", str(tmp_path), str(commit)], text=True, capture_output=True)
    assert bad.returncode == 1
    assert "manifest" in bad.stdout


def test_incomplete_bundle_cannot_create_staged_manifest(tmp_path: Path):
    names = _bundle(tmp_path)
    (tmp_path / names["checksums"]).unlink()
    cmd = [sys.executable, str(SCRIPT), "create", str(tmp_path), "--phase", "9", "--parent", "8"]
    for role, name in names.items():
        cmd += ["--" + role.replace("_", "-"), name]
    result = subprocess.run(cmd, text=True, capture_output=True)
    assert result.returncode == 1
    assert "missing required publication artifact: checksums" in result.stdout


def _committed_phase(archive: Path, phase: str, parent: str, dirname: str | None = None) -> Path:
    directory = archive / (dirname or f"{phase} phase")
    directory.mkdir(parents=True)
    names = _bundle(directory)
    cmd = [sys.executable, str(SCRIPT), "create", str(directory), "--phase", phase, "--parent", parent]
    for role, name in names.items():
        cmd += ["--" + role.replace("_", "-"), name]
    made = subprocess.run(cmd, text=True, capture_output=True, check=True)
    manifest = directory / f"OH-GOON-{phase}-PUBLICATION-MANIFEST.json"
    manifest.write_text(made.stdout)
    commit = directory / f"OH-GOON-{phase}-AUTHORITY-COMMIT.json"
    subprocess.run([sys.executable, str(SCRIPT), "commit", str(directory), str(manifest), str(commit)], check=True)
    return directory


def test_discovery_ignores_higher_uncommitted_phase_directory(tmp_path: Path):
    archive = tmp_path / "archive"
    _committed_phase(archive, "1267.0.961", "1267.0.959")
    staged = archive / "1267.0.999 abandoned staging"
    staged.mkdir()
    (staged / "OH-GOON-1267.0.999-NEXT-SESSION-HANDOFF.md").write_text("claims newest")
    result = subprocess.run([sys.executable, str(SCRIPT), "discover", str(archive)], text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["phase"] == "1267.0.961"


def test_discovery_ignores_invalid_higher_commit(tmp_path: Path):
    archive = tmp_path / "archive"
    _committed_phase(archive, "1267.0.961", "1267.0.959")
    bad = _committed_phase(archive, "1267.0.999", "1267.0.998")
    (bad / "candidate.zip").write_bytes(b"mutated")
    result = subprocess.run([sys.executable, str(SCRIPT), "discover", str(archive)], text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["phase"] == "1267.0.961"


def test_discovery_fails_closed_on_duplicate_valid_committed_phase(tmp_path: Path):
    archive = tmp_path / "archive"
    _committed_phase(archive, "1267.0.961", "1267.0.960", "961-a")
    _committed_phase(archive, "1267.0.961", "1267.0.960", "961-b")
    result = subprocess.run([sys.executable, str(SCRIPT), "discover", str(archive)], text=True, capture_output=True)
    assert result.returncode == 1
    assert "ambiguous committed authority" in result.stdout


def test_discovery_fails_when_no_valid_commit_exists(tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "1267.0.999").mkdir()
    result = subprocess.run([sys.executable, str(SCRIPT), "discover", str(archive)], text=True, capture_output=True)
    assert result.returncode == 1
    assert "no valid committed development authority" in result.stdout
