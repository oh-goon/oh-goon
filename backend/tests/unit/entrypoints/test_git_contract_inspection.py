from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from oh_goon.cli import main


def _commit(root: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "selected source",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: dict[str, str]) -> Path:
    target = tmp_path / "target"
    (target / ".git").mkdir(parents=True)
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(target)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_DB", str(league.parent / "test-state.db"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    workflow = tmp_path / "workflow"
    workflow.mkdir(exist_ok=True)
    selection = workflow / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": source,
                "target": {"repository": str(target)},
            }
        )
    )
    return selection


def _goon(catalog: Path, key: str, declaration: dict[str, object]) -> Path:
    package = catalog / key
    package.mkdir(parents=True)
    (package / "README.md").write_text(f"# {key}\n")
    (package / "goon.yml").write_text(yaml.safe_dump(declaration))
    return package


def test_git_contract_can_be_acquired_before_inputs_and_reports_captured_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    upstream = tmp_path / "upstream"
    package = upstream / "goons/general/orders"
    showcase = Path(__file__).resolve().parents[4] / "templates/goons/general/orders-taskflow"
    shutil.copytree(
        showcase,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    (package / "README.md").write_text("# general/orders\n")
    declaration = yaml.safe_load((package / "goon.yml").read_text())
    declaration["inputs"] = {
        "level": {"type": "integer", "min": 1, "max": 3},
        "count": {"type": "integer", "default": 2},
    }
    declaration["uv"] = {"group": "dev"}
    declaration["steps"] = [{"run": "orders-pipeline --help"}]
    (package / "goon.yml").write_text(yaml.safe_dump(declaration))
    (upstream / "plan.yml").write_text("goons:\n- import: general/orders\n  from: goons\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(upstream),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "selected source",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    target = tmp_path / "target"
    target.mkdir()
    (target / ".git").mkdir()
    league = tmp_path / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(target)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_DB", str(league.parent / "test-state.db"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    selection = workflow / "run.yml"
    payload = {
        "version": 2,
        "source": {
            "kind": "git-plan",
            "git": str(upstream),
            "ref": "main",
            "path": ".",
            "plan": "plan.yml",
        },
        "target": {"repository": str(target)},
    }
    selection.write_text(yaml.safe_dump(payload))
    assert main(["workflow", "inspect", str(selection), "--json"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["code"] == "SOURCE_ACQUISITION_REQUIRED"
    assert "workflow inspect run.yml --acquire" in refused["safe_next_action"]
    from oh_goon.api.schemas.league import SafePlanInputDiagnostic

    assert SafePlanInputDiagnostic.model_validate(refused).code == "SOURCE_ACQUISITION_REQUIRED"
    assert main(["workflow", "inspect", str(selection), "--acquire", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "contract-inspected"
    contracts = {item["name"]: item["contract"] for item in report["inputs"]}
    assert contracts["level"]["required"] is True
    assert contracts["level"]["minimum"] == 1 and contracts["level"]["maximum"] == 3
    assert contracts["count"]["default"] == 2
    assert report["source"]["git"]["commit"] == commit
    assert report["source"]["path"] == f"git:{upstream}@{commit}:plan.yml"
    source = report["occurrences"][0]["source"]
    assert source["path"] == f"git:{upstream}@{commit}:goons/general/orders/goon.yml"
    assert source["project"]["content_sha256"] != source["yaml_sha256"]
    group = source["dependency_selections"][0]
    assert group["declared_group"] == "dev"
    assert group["group"] is None and group["group_state"] == "unresolved"
    assert source["installation"] == "not-established"
    assert source["execution"] == "not-started"
    assert not (workflow / "goon.lock").exists()
    assert "oh-goon-git-" not in json.dumps(report)
    assert main(["workflow", "validate", str(selection), "--acquire", "--json"]) == 2
    validation = json.loads(capsys.readouterr().out)
    assert validation["status"] == "contract-inspected"
    assert validation["diagnostic"]["code"] == "MISSING_REQUIRED_INPUT"
    assert validation["proof"]["admission"] == "not-started"
    payload["inputs"] = {"level": 2}
    selection.write_text(yaml.safe_dump(payload))
    assert main(["workflow", "prepare", str(selection)]) == 0
    capsys.readouterr()
    assert main(["workflow", "inspect", str(selection), "--json"]) == 0
    locked = json.loads(capsys.readouterr().out)
    assert locked["status"] == "inputs-valid"
    assert locked["source"]["git"]["commit"] == commit
    group = locked["occurrences"][0]["source"]["dependency_selections"][0]
    assert group["group"] == "dev" and group["group_state"] == "resolved"
    assert "oh-goon-git-" not in json.dumps(locked)
    assert locked["configuration_evidence"] is not None
    assert main(["workflow", "inspect", str(selection)]) == 0
    human = capsys.readouterr().out
    assert commit in human and "Dependency group: 'dev' | resolved" in human


@pytest.mark.parametrize("private", [False, True])
def test_dependency_groups_use_exact_resolved_occurrences_and_preserve_string_privacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private: bool,
) -> None:
    upstream = tmp_path / "upstream"
    package = upstream / "goons/general/orders"
    showcase = Path(__file__).resolve().parents[4] / "templates/goons/general/orders-taskflow"
    shutil.copytree(
        showcase,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    (package / "README.md").write_text("# general/orders\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "inputs": {
                    "dependency_group": {"type": "string" if private else "repository_path"}
                },
                "uv": {"group": "${{ inputs.dependency_group }}"},
                "steps": [{"id": "execute", "run": "orders-pipeline --help"}],
            }
        )
    )
    commit = _commit(upstream)
    selection = _selection(tmp_path, monkeypatch, {"kind": "file-plan", "path": "plan.yml"})
    groups = ["PRIVATE_GROUP_ALPHA", "PRIVATE_GROUP_BETA"] if private else ["dev", "airflow"]
    origin = {"git": str(upstream), "ref": "main", "path": "goons/general/orders"}
    (selection.parent / "plan.yml").write_text(
        yaml.safe_dump(
            {
                "goons": [
                    {
                        "import": "general/orders",
                        "as": alias,
                        "from": origin,
                        "with": {"dependency_group": group},
                    }
                    for alias, group in zip(("first", "second"), groups, strict=True)
                ],
            }
        )
    )
    assert main(["workflow", "inspect", str(selection), "--json"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["code"] == "SOURCE_ACQUISITION_REQUIRED"
    assert "workflow inspect run.yml --acquire" in refused["safe_next_action"]
    assert str(upstream) not in json.dumps(refused)
    assert main(["workflow", "inspect", str(selection), "--acquire", "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "inputs-valid"
    selections = [item["source"]["dependency_selections"][0] for item in report["occurrences"]]
    assert [item["declared_group"] for item in selections] == ["${{ inputs.dependency_group }}"] * 2
    assert [item["group"] for item in selections] == ([None, None] if private else groups)
    assert {item["group_state"] for item in selections} == {
        "resolved-redacted" if private else "resolved"
    }
    assert {item["source"]["git"]["commit"] for item in report["occurrences"]} == {commit}
    assert "oh-goon-git-" not in output
    if private:
        assert all(group not in output for group in groups)
    assert main(["workflow", "inspect", str(selection), "--acquire"]) == 0
    human = capsys.readouterr().out
    if private:
        assert "resolved-redacted" in human and all(group not in human for group in groups)
    else:
        assert all(f"Dependency group: {group!r} | resolved" in human for group in groups)


def test_git_plan_nested_local_and_remote_goons_keep_logical_paths_and_shipped_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    remote = tmp_path / "remote"
    _goon(remote / "catalog", "general/leaf", {"steps": [{"run": "echo selected"}]})
    _goon(
        remote / "catalog",
        "general/wrapper",
        {
            "goons": [{"import": "general/leaf", "from": "../.."}],
        },
    )
    remote_commit = _commit(remote)
    upstream = tmp_path / "upstream"
    root = upstream / "examples/demo"
    _goon(
        root / "goons",
        "general/composite",
        {
            "goons": [
                {"import": "general/local", "from": "../.."},
                "python/quality",
                {
                    "import": "general/wrapper",
                    "from": {
                        "git": str(remote),
                        "ref": "main",
                        "path": "catalog/general/wrapper",
                    },
                },
            ]
        },
    )
    _goon(root / "goons", "general/local", {"steps": [{"run": "echo local"}]})
    plan = root / "plans/selected.yml"
    plan.parent.mkdir()
    plan.write_text("goons:\n- import: general/composite\n  from: ../goons\n")
    commit = _commit(upstream)
    selection = _selection(
        tmp_path,
        monkeypatch,
        {
            "kind": "git-plan",
            "git": str(upstream),
            "ref": "main",
            "path": "examples/demo",
            "plan": "plans/selected.yml",
        },
    )
    assert main(["workflow", "inspect", str(selection), "--acquire", "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "inputs-valid"
    assert report["source"]["path"] == f"git:{upstream}@{commit}:examples/demo/plans/selected.yml"
    sources = {item["goon"]: item["source"] for item in report["occurrences"]}
    assert (
        sources["general/composite"]["path"]
        == f"git:{upstream}@{commit}:examples/demo/goons/general/composite/goon.yml"
    )
    assert (
        sources["general/local"]["path"]
        == f"git:{upstream}@{commit}:examples/demo/goons/general/local/goon.yml"
    )
    assert (
        sources["general/wrapper"]["path"]
        == f"git:{remote}@{remote_commit}:catalog/general/wrapper/goon.yml"
    )
    assert (
        sources["general/leaf"]["path"]
        == f"git:{remote}@{remote_commit}:catalog/general/leaf/goon.yml"
    )
    assert sources["python/quality"]["path"] == "catalog:python/quality/goon.yml"
    assert sources["python/quality"]["git"] is None
    assert sources["python/ruff"]["path"] == "catalog:python/ruff/goon.yml"
    assert "oh-goon-git-" not in output
    assert report["configuration_evidence"] is not None
