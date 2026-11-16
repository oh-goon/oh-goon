"""Installed CLI acceptance with an explicit PATH containing no frontend tools.

The release owner supplies the already installed command and isolated environment.
Native tool lookup, uv execution, CLI admission and recorded Runs keep their own
authority; this journey neither installs tools nor starts an application server.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CAPTURE_BYTES = 2 * 1024 * 1024
_FRONTEND_TOOLS = (
    "node",
    "nodejs",
    "npm",
    "npx",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "firefox",
    "chromedriver",
)
CliRunner = Callable[..., subprocess.CompletedProcess[object]]


@dataclass(frozen=True, slots=True)
class InstalledCliCommandFact:
    argv: tuple[str, ...]
    exit_code: int
    duration_ms: int
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class InstalledCliRuntimeAcceptance:
    issues: tuple[str, ...] = ()
    commands: tuple[InstalledCliCommandFact, ...] = ()
    absent_tools: tuple[str, ...] = ()
    native_versions: tuple[tuple[str, str], ...] = ()
    run_id: str | None = None
    missing_uv_run_id: str | None = None
    state_free_actions: bool = False
    fresh_readback_verified: bool = False
    database_identity_preserved: bool = False
    provenance_verified: bool = False
    receiving_path: tuple[str, ...] = ()


def _bounded_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    runner: CliRunner | None,
) -> tuple[InstalledCliCommandFact, str, str]:
    started = time.monotonic()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        # This is a control-CLI launch, like the release owner's origin/server
        # probes. A workload runner intentionally strips DB/League authority.
        selected_runner = subprocess.run if runner is None else runner
        completed = selected_runner(
            tuple(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=120,
            check=False,
        )
        exit_code = completed.returncode
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(_CAPTURE_BYTES + 1), errors.read(_CAPTURE_BYTES + 1)
    if len(stdout) > _CAPTURE_BYTES or len(stderr) > _CAPTURE_BYTES:
        raise RuntimeError("installed CLI acceptance output exceeded its capture bound")
    fact = InstalledCliCommandFact(
        tuple(argv),
        exit_code,
        int((time.monotonic() - started) * 1000),
        len(stdout),
        len(stderr),
        hashlib.sha256(stdout).hexdigest(),
        hashlib.sha256(stderr).hexdigest(),
    )
    return fact, stdout.decode("utf-8"), stderr.decode("utf-8")


def exercise_installed_cli_runtime(
    command: Path,
    temp: Path,
    env: Mapping[str, str],
    *,
    expected_version: str,
    runner: CliRunner | None = None,
) -> InstalledCliRuntimeAcceptance:
    """Consume one existing installation; absence is scoped to the selected PATH."""
    commands: list[InstalledCliCommandFact] = []
    absent: tuple[str, ...] = ()
    versions: tuple[tuple[str, str], ...] = ()
    run_id = missing_run_id = None
    state_free = readback = database_preserved = False
    receiving_path: tuple[str, ...] = ()

    def finish(*issues: str) -> InstalledCliRuntimeAcceptance:
        return InstalledCliRuntimeAcceptance(
            issues=issues,
            commands=tuple(commands),
            absent_tools=absent,
            native_versions=versions,
            run_id=run_id,
            missing_uv_run_id=missing_run_id,
            state_free_actions=state_free,
            fresh_readback_verified=readback,
            database_identity_preserved=database_preserved,
            receiving_path=receiving_path,
        )

    selected = dict(env)

    def invoke(
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        expected: int | None = 0,
        native: bool = False,
    ) -> str:
        argv = tuple(arguments) if native else (str(command), *arguments)
        fact, stdout, _stderr = _bounded_command(
            argv,
            cwd=temp,
            env=environment or selected,
            runner=runner,
        )
        commands.append(fact)
        if expected is not None and fact.exit_code != expected:
            raise RuntimeError(
                f"installed CLI acceptance command {tuple(arguments)!r} exited {fact.exit_code}, expected {expected}"
            )
        return stdout

    def document(arguments: Sequence[str], **options: Any) -> dict[str, Any]:
        value = json.loads(invoke(arguments, **options))
        if not isinstance(value, dict):
            raise RuntimeError("installed CLI acceptance expected a JSON object")
        return value

    def step(tape: dict[str, Any]) -> dict[str, Any]:
        repositories = tape.get("repositories")
        if not isinstance(repositories, list) or len(repositories) != 1:
            raise RuntimeError("installed CLI Run did not retain its one repository")
        steps = repositories[0].get("steps")
        if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
            raise RuntimeError("installed CLI Run did not retain its one Step")
        return steps[0]

    try:
        tools = temp / "native-tools"
        tools.mkdir()
        native_tools: dict[str, Path] = {}
        for name in ("uv", "git"):
            located = shutil.which(name, path=env.get("PATH", os.defpath))
            if located is None:
                return finish(f"installed CLI PATH acceptance requires native {name}")
            native_tools[name] = Path(located).absolute()
            (tools / name).symlink_to(native_tools[name])
        selected.update(
            PATH=os.pathsep.join((str(command.parent), str(tools))),
            UV_PYTHON=str(command.with_name("python.exe" if os.name == "nt" else "python")),
            UV_PYTHON_DOWNLOADS="never",
            UV_OFFLINE="1",
            UV_CACHE_DIR=str(temp / "uv-cache"),
            NO_COLOR="1",
            LANG="C.UTF-8",
            LC_ALL="C.UTF-8",
        )
        if any(shutil.which(name, path=selected["PATH"]) is not None for name in _FRONTEND_TOOLS):
            return finish("frontend tools are discoverable on the isolated installed CLI PATH")
        absent = _FRONTEND_TOOLS
        database = Path(selected["OH_GOON_DB"])
        league = Path(selected["OH_GOON_LEAGUE_FILE"])
        league_before = None
        if league.exists():
            with league.open("rb") as stream:
                league_before = hashlib.file_digest(stream, "sha256").hexdigest()
        if database.exists():
            return finish(
                "installed CLI journey requires fresh isolated state; existing state was preserved"
            )
        if "oh-goon" not in invoke(("--help",)):
            return finish("installed CLI help did not describe oh-goon")
        if invoke(("--version",)).strip() != expected_version:
            return finish("installed CLI version differs from the selected release")
        doctor = document(("doctor", "--json"))
        observed_tools = {row["name"]: row for row in doctor.get("tools", [])}
        if (
            doctor.get("scope") != "tools"
            or doctor.get("status") != "ready"
            or any(observed_tools.get(name, {}).get("status") != "READY" for name in ("git", "uv"))
        ):
            return finish("installed CLI doctor did not establish its native git/uv tools")
        versions = tuple((name, str(observed_tools[name]["version"])) for name in ("git", "uv"))
        catalog = document(("templates", "list", "--json"))
        if catalog.get("schema") != "oh-goon.template-catalog/v1" or not catalog.get("templates"):
            return finish("installed CLI could not browse its shipped template catalog")

        repository = temp / "repositories" / "cli-target"
        repository.mkdir(parents=True)
        invoke(("git", "init", "-q", str(repository)), native=True)
        (repository / "cli_acceptance_step.py").write_text(
            "from pathlib import Path\nimport json,os,shutil\n"
            "counter=Path('cli-execution-count.txt')\n"
            "counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')\n"
            f"tools={{name:shutil.which(name) for name in {_FRONTEND_TOOLS!r}}}\n"
            "Path('cli-result.json').write_text(json.dumps({'frontend_tools':tools,'path':os.environ['PATH']}))\n"
            "print('installed CLI native step passed', flush=True)\n",
            encoding="utf-8",
        )
        workflow = temp / "workflow"
        package = workflow / "goons/general/installed-cli"
        package.mkdir(parents=True)
        (package / "README.md").write_text("# general/installed-cli\n", encoding="utf-8")
        (package / "goon.yml").write_text(
            "steps:\n- id: cli-native-step\n"
            "  run: uv run --offline --no-project python cli_acceptance_step.py\n"
            "  artifacts:\n  - path: cli-result.json\n",
            encoding="utf-8",
        )
        (workflow / "plan.yml").write_text(
            "goons:\n- import: general/installed-cli\n  from: goons\n",
            encoding="utf-8",
        )
        configuration = workflow / "run.yml"
        configuration.write_text(
            "version: 2\nsource: {kind: file-plan, path: plan.yml}\n"
            "target: {repository: ../repositories/cli-target}\n",
            encoding="utf-8",
        )
        inspected = document(("workflow", "inspect", str(configuration), "--json"))
        if inspected.get("schema") != "oh-goon.run-configuration/v2":
            return finish("installed CLI workflow inspect omitted its original source report")
        lock = workflow / "goon.lock"
        league_after = None
        if league.exists():
            with league.open("rb") as stream:
                league_after = hashlib.file_digest(stream, "sha256").hexdigest()
        if database.exists() or lock.exists() or league_after != league_before:
            return finish(
                "installed CLI read-only actions initialized state, changed League registration or wrote a lock"
            )
        state_free = True
        prepared = document(
            ("workflow", "prepare", str(configuration), "--register-target", "--no-input", "--json")
        )
        if prepared.get("status") != "prepared" or not lock.is_file():
            return finish("installed CLI workflow prepare did not publish its lock")
        locked_bytes = lock.read_bytes()
        executed = document(("run", str(configuration), "--no-input", "--yes", "--json"))
        run_id = executed.get("fleet_run_id")
        if (
            not isinstance(run_id, str)
            or executed.get("status") != "COMPLETED"
            or executed.get("outcome") != "SUCCESS"
        ):
            return finish("installed CLI did not complete its original attended Run successfully")
        positive_show = document(("runs", "show", run_id, "--json"))
        positive_tape = document(("runs", "tape", run_id, "--json"))
        positive_outputs = document(("runs", "outputs", run_id, "--json"))
        positive_step = step(positive_tape)
        if (
            positive_show.get("outcome") != "SUCCESS"
            or positive_step.get("settlement", {}).get("outcome") != "SUCCESS"
        ):
            return finish("fresh installed CLI readback changed the successful Run")
        artifacts = step(positive_outputs).get("artifacts")
        if (
            not isinstance(artifacts, list)
            or len(artifacts) != 1
            or artifacts[0].get("status") != "PRESENT"
        ):
            return finish("fresh installed CLI output readback omitted native artifact evidence")
        counter = repository / "cli-execution-count.txt"
        if counter.read_text() != "1":
            return finish("installed CLI did not execute the native stdlib workload exactly once")
        received = json.loads((repository / "cli-result.json").read_text(encoding="utf-8"))
        if set(received.get("frontend_tools", {})) != set(_FRONTEND_TOOLS) or any(
            value is not None for value in received["frontend_tools"].values()
        ):
            return finish("frontend tools were discoverable inside the original native Step")
        receiving_path = tuple(received["path"].split(os.pathsep))
        before = database.stat()
        (tools / "uv").unlink()
        if shutil.which("uv", path=selected["PATH"]) is not None:
            return finish("uv remained discoverable on the negative runtime PATH")
        missing_doctor = document(("doctor", "--json"), expected=2)
        missing_tools = {row["name"]: row for row in missing_doctor.get("tools", [])}
        if (
            missing_doctor.get("status") != "setup_required"
            or missing_tools.get("uv", {}).get("reason_code") != "TOOL_MISSING"
        ):
            return finish("missing-uv doctor did not preserve the native tool refusal")
        negative = document(
            ("run", str(configuration), "--no-input", "--yes", "--json"), expected=None
        )
        missing_run_id = negative.get("fleet_run_id")
        if (
            commands[-1].exit_code == 0
            or not isinstance(missing_run_id, str)
            or missing_run_id == run_id
        ):
            return finish("missing uv did not create a distinct truthful non-success Run")
        negative_tape = document(("runs", "tape", missing_run_id, "--json"))
        negative_step = step(negative_tape)
        settlement = negative_step.get("settlement", {})
        if (
            negative.get("outcome") != "ATTENTION_REQUIRED"
            or settlement.get("outcome") != "FAILED"
            or settlement.get("reason") != "FAILED_TO_START"
            or settlement.get("observed_returncode") is not None
        ):
            return finish("missing uv did not retain the original native launch-refusal settlement")
        if lock.read_bytes() != locked_bytes or counter.read_text() != "1":
            return finish("missing uv rewrote its lock or replayed the native workload")
        after = database.stat()
        database_preserved = (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        if not database_preserved:
            return finish("missing uv replaced the original runtime database")
        historical = document(("runs", "show", run_id, "--json"))
        historical_step = step(document(("runs", "tape", run_id, "--json")))
        historical_outputs = step(document(("runs", "outputs", run_id, "--json")))
        if (
            historical.get("outcome") != "SUCCESS"
            or historical_step != positive_step
            or historical_outputs.get("artifacts") != artifacts
        ):
            return finish("missing uv rewrote the prior Run, Step or artifact history")
        readback = True
        return finish()
    except (
        OSError,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        return finish(f"installed CLI runtime journey: {error}")
