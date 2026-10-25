"""Opt-in live Codex CLI execution of initialized shipped Plan/Goon workflows.

Select the public executable and existing native account directory explicitly.
Only Codex reads its authentication; the test never opens auth or env files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from oh_goon.cli import main


@pytest.mark.certification
@pytest.mark.skipif(os.name != "posix", reason="Native POSIX project verification script")
@pytest.mark.parametrize("kind", ["plan", "goon"])
@pytest.mark.parametrize("connection", ["codex_login", "codex_service"])
def test_live_codex_executes_initialized_native_workflow_and_independent_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    connection: str,
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    account_root = os.environ.get("OH_GOON_LIVE_CODEX_HOME")
    if not executable or not account_root:
        pytest.skip("Select the installed Codex executable and its existing sign-in directory")
    program = Path(executable).resolve(strict=True)
    selected_account = Path(account_root).resolve(strict=True)
    assert selected_account.is_dir()
    definition: dict[str, object] = {"executable": str(program)}
    if connection == "codex_service":
        runtime_root = os.environ.get("OH_GOON_NATIVE_CODEX_RUNTIME_ROOT")
        if not runtime_root:
            pytest.skip("Select the complete public runtime for native service execution")
        public_runtime = Path(runtime_root).resolve(strict=True)
        assert program.is_relative_to(public_runtime)
        assert not public_runtime.is_relative_to(selected_account)
        assert not selected_account.is_relative_to(public_runtime)
        definition["runtime_roots"] = [str(public_runtime)]
    repository = tmp_path / "repos" / "sample"
    repository.mkdir(parents=True)
    source = repository / "arithmetic.py"
    source.write_text("def double(value):\n    return value + 2\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)

    verifier = tmp_path / "independent" / "verify.py"
    verifier.parent.mkdir()
    verifier.write_text(
        "import pathlib, runpy, sys\n"
        "double = runpy.run_path(str(pathlib.Path(sys.argv[1]) / 'arithmetic.py'))['double']\n"
        "assert all(double(value) == value * 2 for value in range(-20, 21))\n"
        "print('independent-arithmetic-verification-passed')\n"
    )
    verifier_bytes = verifier.read_bytes()
    verification = [sys.executable, "-I", "-B", str(verifier), str(repository)]
    baseline = subprocess.run(verification, capture_output=True, text=True, timeout=10)
    assert baseline.returncode != 0
    scripts = repository / "scripts"
    scripts.mkdir()
    project_check = scripts / "verify"
    project_check.write_text(
        f"#!{sys.executable}\nimport subprocess\n"
        f"raise SystemExit(subprocess.run({verification!r}, check=False).returncode)\n"
    )
    project_check.chmod(0o755)
    project_check_bytes = project_check.read_bytes()

    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "runtime.sqlite"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    providers = tmp_path / "providers.json"
    providers.write_text(
        json.dumps(
            {
                "native_agents": {"codex": definition},
                "native_profiles": {
                    "live-cli": {
                        "backend": "local",
                        "allowed_agents": ["codex"],
                        "workspace_roots": [str(repository.parent)],
                        "timeout_seconds": 180,
                        connection: {
                            "home": str(selected_account),
                            "model": os.environ.get("OH_GOON_LIVE_CODEX_MODEL", "gpt-6-luna"),
                            "reasoning_effort": "low",
                        },
                    }
                },
            }
        )
    )
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(providers))
    assert main(["league", "add-folder", str(repository.parent)]) == 0
    capsys.readouterr()
    assert main(["league", "scan", "--json"]) == 0
    league = json.loads(capsys.readouterr().out)
    target = league["teams"][0]["repository"]
    assert Path(target["path"]) == repository
    workflow = tmp_path / "workflow"
    assert (
        main(
            [
                "templates",
                "init",
                "agents/native-coding",
                "--kind",
                kind,
                "--output",
                str(workflow),
            ]
        )
        == 0
    )
    if kind == "goon":
        with (workflow / "plan.yml").open("a") as stream:
            stream.write("- agents/verification\n")
    skill = workflow / "skills" / "repair"
    skill.mkdir(parents=True)
    skill_text = "# Arithmetic repair\nRepair the requested function. Read no auth or env files.\n"
    (skill / "SKILL.md").write_text(skill_text)
    selection = workflow / "run.yml"
    payload = yaml.safe_load(selection.read_text())
    payload["target"]["repository_id"] = target["repository_id"]
    payload["inputs"] = {
        "repo": "local:sample",
        "skill": "local:skills/repair",
        "goal": "Fix arithmetic.double to multiply its integer input by two. "
        "Change arithmetic.py only. Do not read auth files or env files.",
        "verification_command": "scripts/verify",
    }
    payload["agent"] = {
        "implementation": "codex",
        "profile": "live-cli",
        "acceptance": "native-coding",
        "repository_root": str(repository.parent),
        "skill_root": ".",
    }
    selection.write_text(yaml.safe_dump(payload, sort_keys=False))
    assert main(["config", "validate", str(selection), "--json"]) == 0
    assert main(["lock", str(selection)]) == 0
    capsys.readouterr()
    assert main(["run", str(selection), "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
    assert main(["plan", "tape", receipt["fleet_run_id"], "--json"]) == 0
    tape = json.loads(capsys.readouterr().out)
    steps = tape["repositories"][0]["steps"]
    assert tape["status"] == "PASSED"
    assert len(steps) == 2
    assert all(step["status"] == "PASSED" and step["exit_code"] == 0 for step in steps)
    assert steps[0]["native_agent_observations"][0]["agent"] == "codex"
    if connection == "codex_service":
        execution = steps[0]["native_agent_observations"][0]["execution"]
        assert execution["authority"] == "ORIGINAL_STEP_EXECUTION"
        assert execution["native_version"] == "0.164.0"
        assert execution["repository_access"] == "write"
        assert execution["native_state_separation"] == "verified"
        assert execution["preflight"] == "verified"
        assert execution["mcp_catalog"] == "empty"
        assert execution["process_cleanup"] == "verified"
    assert tuple(steps[1]["command"]) == ("./scripts/verify",)
    completed = subprocess.run(verification, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0
    assert "independent-arithmetic-verification-passed" in completed.stdout
    assert verifier.read_bytes() == verifier_bytes
    assert project_check.read_bytes() == project_check_bytes
    assert (skill / "SKILL.md").read_text() == skill_text
