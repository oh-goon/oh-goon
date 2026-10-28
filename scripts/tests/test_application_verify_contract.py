from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/application_verify.sh"


def _isolated_runner(
    directory: Path, script_text: str, *, prepared: bool = True
) -> Path:
    scripts = directory / "checkout/scripts"
    scripts.mkdir(parents=True)
    script = scripts / SCRIPT.name
    script.write_text(script_text, encoding="utf-8")
    shutil.copy2(ROOT / "scripts/command_banner.sh", scripts / "command_banner.sh")
    if prepared:
        owner = scripts.parent / "backend/.venv/bin/oh-goon-source-layout"
        owner.parent.mkdir(parents=True)
        owner.symlink_to(ROOT / "backend/.venv/bin/oh-goon-source-layout")
    return script


def _verify_rows() -> list[tuple[str, str, str]]:
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    rows = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        owner, label, target = line.split("|", 2)
        rows.append((owner, label, target))
    return rows


def test_verify_is_broader_than_lean_certification_and_keeps_browser_diagnostics() -> (
    None
):
    rows = _verify_rows()
    targets = [row[2] for row in rows]
    assert targets[:5] == [
        "test-python-complete",
        "test-tooling-complete",
        "architecture",
        "check-generated",
        "test-integration",
    ]
    assert "verify-p1" in targets
    assert "browser-targeted-certification" in targets
    assert "browser-required-certification" in targets
    assert "certification-workspace-setup" not in targets
    assert len(set(targets)) == len(targets)


def test_verify_goon_identity_is_provenance_not_scheduling_authority(
    tmp_path: Path,
) -> None:
    original = SCRIPT.read_text(encoding="utf-8")
    renamed = original.replace(
        "oh-goon/frontend-functional", "oh-goon/frontend-functional-renamed"
    ).replace("oh-goon/browser-", "oh-goon/browser-renamed-")

    def run(script_text: str, directory: Path) -> tuple[int, list[str], str]:
        script = _isolated_runner(directory, script_text)
        checkout = script.parent.parent
        calls = directory / "calls.txt"
        fake_make = directory / "make"
        fake_make.write_text(
            "#!/bin/sh\n"
            "target=''\n"
            'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
            f"printf '%s\\n' \"$target\" >> {calls}\n"
            'case "$target" in frontend-functional-certification) exit 19 ;; *) exit 0 ;; esac\n',
            encoding="utf-8",
        )
        fake_make.chmod(0o755)
        completed = subprocess.run(
            ["bash", str(script)],
            cwd=checkout,
            env={**os.environ, "MAKE": str(fake_make)},
            text=True,
            capture_output=True,
        )
        return (
            completed.returncode,
            calls.read_text(encoding="utf-8").splitlines(),
            completed.stdout,
        )

    original_result = run(original, tmp_path / "original")
    renamed_result = run(renamed, tmp_path / "renamed")

    assert original_result[0] == renamed_result[0] == 1
    assert original_result[1] == renamed_result[1]
    assert "browser-harness-preflight" in original_result[1]
    assert "RESULT: FAILED" in original_result[2]
    assert "RESULT: FAILED" in renamed_result[2]


def test_verify_uses_native_init_as_its_only_materialization_prerequisite(
    tmp_path: Path,
) -> None:
    script = _isolated_runner(tmp_path, SCRIPT.read_text(encoding="utf-8"))
    calls = tmp_path / "calls.txt"
    fake_make = tmp_path / "make"
    fake_make.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        f"printf '%s\\n' \"$target\" >> {calls}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env={**os.environ, "MAKE": str(fake_make)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout
    targets = calls.read_text(encoding="utf-8").splitlines()
    assert targets[0] == "init"
    assert targets[1] == "candidate-convergence-preflight"
    assert "certification-setup" not in targets
    assert "certification-workspace-setup" not in targets


def test_verify_blocks_broad_diagnostics_when_native_init_fails(tmp_path: Path) -> None:
    fake_make = tmp_path / "make"
    fake_make.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'case "$target" in init) exit 17 ;; *) exit 0 ;; esac\n',
        encoding="utf-8",
    )
    fake_make.chmod(0o755)
    script = _isolated_runner(
        tmp_path, SCRIPT.read_text(encoding="utf-8"), prepared=False
    )
    isolated = script.parent.parent
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=isolated,
        env={**os.environ, "MAKE": str(fake_make)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 1
    assert "BLOCKED" in completed.stdout
    assert "native make init failed exit 17" in completed.stdout


def test_verify_explicitly_denies_certification_authority() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "never creates APP PASS" in text
    assert "no APP certification PASS was created" in text
    assert "This is not certification authority" in text
