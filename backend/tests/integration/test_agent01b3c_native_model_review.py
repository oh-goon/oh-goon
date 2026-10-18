"""AGENT-01B3C real-model/real-Hashmarks qualification on Linux or WSL.

No Hashmarks identity replacement, fake Ollama command, or Fleet execution patch.
The dedicated qualification entrypoint requires both native dependencies; ordinary
hosted suites skip rather than pretending to qualify installed model inference.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tomllib
import uuid
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime import goon_templates
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

MODEL_ENV = "OH_GOON_AGENT_MODEL"
REQUIRED_ENV = "OH_GOON_REQUIRE_NATIVE_AGENT_MODEL"


def _locked_hashmarks_version() -> str:
    """Read the version from the existing uv.lock authority, not a second pin."""
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
    versions = [row["version"] for row in packages if row["name"] == "hashmarks"]
    assert len(versions) == 1, "exactly one Hashmarks authority is required in uv.lock"
    return versions[0]


def _require_real_native_dependencies() -> str:
    def unavailable(why: str) -> None:
        if os.environ.get(REQUIRED_ENV) == "1":
            pytest.fail(f"AGENT-01B3C NATIVE UNQUALIFIED: {why}", pytrace=False)
        pytest.skip(f"AGENT-01B3C native model qualification not run: {why}")

    if os.name != "posix" or not Path("/proc").is_dir():
        unavailable("requires Linux/WSL")
    for executable in ("git", "rg", "ollama"):
        if shutil.which(executable) is None:
            unavailable(f"real {executable} executable unavailable")
    model = os.environ.get(MODEL_ENV, "").strip()
    if not model or model.startswith("-") or any(c.isspace() for c in model):
        unavailable(f"explicit installed model required via {MODEL_ENV}")
    if importlib.util.find_spec("hashmarks") is None:
        unavailable("pinned Hashmarks wheel is not installed")
    from hashmarks import __version__

    from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

    locked_version = _locked_hashmarks_version()
    if __version__ != locked_version:
        unavailable(f"Hashmarks version mismatch: {__version__} != {locked_version}")
    try:
        producer = require_loaded_hashmarks_runtime()
    except Exception as exc:
        unavailable(f"real installed Hashmarks identity unavailable: {exc}")
    if not producer:
        unavailable("Hashmarks producer implementation identity is unavailable")
    try:
        ready = subprocess.run(
            ["ollama", "show", model],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env={**os.environ, "OLLAMA_HOST": "http://127.0.0.1:11434"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        unavailable(f"local Ollama model not ready: {exc}")
    if ready.returncode != 0:
        unavailable(f"ollama show {model} failed (is the model installed and server running?)")
    return model


@pytest.fixture(scope="module")
def actual_model() -> str:
    return _require_real_native_dependencies()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, capture_output=True, text=True, check=True)


def _native_agent_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str):
    repository = tmp_path / "repositories" / "team" / "app"
    repository.mkdir(parents=True)
    (repository / "marker.txt").write_text(
        "OH_GOON_NATIVE_MODEL_NEEDLE is a fixed marker in a frozen repository.\n",
        encoding="utf-8",
    )
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Agent Native Qualification")
    _git(repository, "config", "user.email", "native-test@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "native-model-check")

    skill = tmp_path / "skills" / "team" / "search"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "# Offline repository review\n"
        "Select a fixed-string search for OH_GOON_NATIVE_MODEL_NEEDLE. "
        "Then evaluate the observation and finish. Follow the exact JSON response schema.\n",
        encoding="utf-8",
    )
    goon = tmp_path / "goons" / "agent" / "search"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("Native model review of frozen repository\n", encoding="utf-8")
    (goon / "goon.yml").write_text(
        "inputs:\n"
        "  repo: {type: repository_reference}\n"
        "  skill: {type: skill_reference}\n"
        "  acceptance: {type: acceptance_profile}\n"
        "steps:\n"
        "  - uses: agent\n"
        "    harness: local-model-review\n"
        "    task: 'Find the exact OH_GOON_NATIVE_MODEL_NEEDLE marker in frozen source and summarize.'\n"
        "    timeout_seconds: 180\n"
        "    requested_effects:\n"
        "      repository_access: read_only\n"
        "      recipients: [ollama_local]\n"
        f"      model: {json.dumps(model)}\n",
        encoding="utf-8",
    )
    plan = tmp_path / "plan.yml"
    plan.write_text(
        "inputs: [repo, skill, acceptance]\n"
        "execution: {purpose: verification}\n"
        "goons: [agent/search]\n",
        encoding="utf-8",
    )
    # Only the Goon source location is injected for this test. Hashmarks, model
    # CLI, original Fleet worker, process supervision, journal, and Game Tape
    # are all their production implementations.
    monkeypatch.setattr(goon_templates, "default_goon_templates_root", lambda: tmp_path / "goons")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runs.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    options = dict(
        inputs={"repo": "local:team/app", "skill": "local:team/search", "acceptance": "review"},
        agent_repository_root=tmp_path / "repositories",
        agent_skill_root=tmp_path / "skills",
        allowed_agent_acceptance=("review",),
    )
    return plan, repository, options


@pytest.mark.parametrize("harness", ["local-model-review", "local-model-skill-loop"])
def test_real_hashmarks_and_installed_ollama_review_survives_restart(
    actual_model: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
):
    plan, repository, opts = _native_agent_source(tmp_path, monkeypatch, actual_model)
    if harness == "local-model-skill-loop":
        goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
        goon.write_text(
            goon.read_text().replace(
                "harness: local-model-review", "harness: local-model-skill-loop"
            )
        )
        skill = tmp_path / "skills" / "team" / "search"
        (skill / "SKILL.md").write_text(
            "# Offline agent review\n"
            "Choose the read_file tool for repo/marker.txt. After observing the "
            "captured marker, return a finish JSON object with a brief summary. "
            "Do not request any other tools or shell commands.\n",
            encoding="utf-8",
        )
    resolved, frozen, _ = FleetPlanResolver(
        goon_templates=GoonTemplateStore(),
    ).resolve_frozen_local_search_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert frozen.requested_effects.model == actual_model
    write_execution_lock(plan.parent / "goon.lock", build_execution_lock(resolved))
    run_id = "agent01b3g-native-" + uuid.uuid4().hex[:12]

    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        admitted = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id=run_id,
            **opts,
        )
        terminal = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=240)
        detail = runtime.runs.get(admitted.fleet_run_id)
        assert detail.game_tape.status.value == "PASSED", (
            f"Actual installed model did not meet strict read-only JSON protocol: {detail.outcome}"
        )
        assert terminal.outcome.value == "SUCCESS"
        step = detail.operations[0].evidence.plan.steps[0]
        assert step.kind == "agent" and step.returncode == 0
        assert step.stdout_evidence.byte_count > 0
        assert step.stdout_evidence.sha256
    finally:
        runtime.shutdown(wait_for_runs=True)

    # Reopen the ORIGINAL SQLite/Game Tape; success is not an in-memory fixture.
    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        persisted = reopened.runs.get(run_id)
        assert persisted.game_tape.status.value == "PASSED"
        assert (
            persisted.operations[0].evidence.plan.steps[0].stdout_evidence.sha256
            == step.stdout_evidence.sha256
        )
    finally:
        reopened.shutdown(wait_for_runs=True)
