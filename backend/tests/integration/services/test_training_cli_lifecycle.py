"""Native Iris training uses the original CLI, Run and retained-artifact owners."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from tests.factories.run_resources import require_native_run_resource_context

ROOT = Path(__file__).resolve().parents[4]
IRIS_PROJECT = ROOT / "templates/goons/ml/iris-train"
METRICS = "ml-output/iris-training-metrics.json"
MODEL = "ml-output/iris-model-parameters.json"
pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(
        sys.platform != "linux" or shutil.which("uv") is None,
        reason="native Linux process and uv observations required",
    ),
]


@dataclass(frozen=True)
class TrainingWorkflow:
    root: Path
    repository: Path
    package: Path
    selection: Path
    environment: dict[str, str]

    @property
    def executable(self) -> Path:
        return Path(sys.executable).with_name("oh-goon")

    def cli(self, *arguments: str, expected: int = 0) -> tuple[dict[str, Any], str]:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            completed = subprocess.run(
                [str(self.executable), *arguments, "--json"],
                cwd=self.root,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                timeout=120,
            )
            output.seek(0)
            errors.seek(0)
            body = output.read(512 * 1024 + 1)
            diagnostic = errors.read(128 * 1024 + 1)
        assert len(body) <= 512 * 1024 and len(diagnostic) <= 128 * 1024
        assert completed.returncode == expected, (arguments, body[-8192:], diagnostic[-8192:])
        value = json.loads(body)
        assert isinstance(value, dict)
        return value, diagnostic.decode("utf-8", errors="replace")

    def prepare(self, **inputs: str | int) -> None:
        selected = yaml.safe_load(self.selection.read_text())
        selected["inputs"].update(inputs)
        self.selection.write_text(yaml.safe_dump(selected, sort_keys=False))
        self.cli("workflow", "prepare", str(self.selection), "--register-target")

    def tape(self, run_id: str) -> dict[str, Any]:
        return self.cli("runs", "tape", run_id)[0]

    def exported(self, run_id: str, label: str) -> dict[str, tuple[str, dict[str, Any]]]:
        recorded = self.cli("runs", "outputs", run_id)[0]
        step = recorded["repositories"][0]["steps"][0]
        artifacts = {row["path"]: row for row in step["artifacts"]}
        assert set(artifacts) == {METRICS, MODEL}
        documents = {}
        for path, artifact in artifacts.items():
            assert artifact["status"] == "PRESENT" and artifact["retained_object"] is not None
            destination = self.root / f"{label}-{Path(path).name}"
            self.cli(
                "runs",
                "export-artifact",
                run_id,
                "--step",
                step["step_id"],
                "--path",
                path,
                "--output",
                str(destination),
            )
            payload = destination.read_bytes()
            assert len(payload) == artifact["byte_count"]
            assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
            documents[path] = (artifact["sha256"], json.loads(payload))
        return documents


def _workflow(tmp_path: Path, *, resources: CicdResourcePolicy | None = None) -> TrainingWorkflow:
    repository = tmp_path / "repositories/subject"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "README.md").write_text("# Independent training subject\n")
    authoring = tmp_path / "workflow"
    package = authoring / "goons/ml/iris-train"
    shutil.copytree(
        IRIS_PROJECT,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "ml-output"),
    )
    (package / ".venv").mkdir()
    (package / ".venv/developer-sentinel").write_text("developer environment is separate\n")
    (authoring / "plan.yml").write_text("goons:\n- import: ml/iris-train\n  from: goons\n")
    selection = authoring / "run.yml"
    selected: dict[str, Any] = {
        "version": 2,
        "source": {"kind": "file-plan", "path": "plan.yml"},
        "target": {"repository": str(repository)},
        "inputs": {"training_rounds": 3, "random_seed": 42, "minimum_accuracy_percent": 90},
    }
    if resources is not None:
        selected["resources"] = resources.model_dump(exclude_none=True)
    selection.write_text(yaml.safe_dump(selected, sort_keys=False))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never",
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
    )
    return TrainingWorkflow(tmp_path, repository, package, selection, environment)


def _training_step(tape: dict[str, Any]) -> dict[str, Any]:
    steps = tape["repositories"][0]["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert isinstance(step, dict)
    assert step["attempt_count"] <= 1 and not step["attempt_transitions"]
    assert step["uv"].get("group") is None
    return step


def _native_timeout_workflow(workflow: TrainingWorkflow) -> TrainingWorkflow:
    """Select the existing repository-native UV lane for a host utility wrapper."""
    project = workflow.root / "repositories/native-timeout-training"
    shutil.copytree(
        IRIS_PROJECT,
        project,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "ml-output"),
    )
    subprocess.run(
        ["git", "init", "-q", str(project)], env=workflow.environment, check=True, timeout=10
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "add",
            "README.md",
            "pyproject.toml",
            "uv.lock",
            "src",
            "tests",
            "goon.yml",
        ],
        env=workflow.environment,
        check=True,
        timeout=10,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=Training fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "captured native Iris project",
        ],
        env=workflow.environment,
        check=True,
        timeout=10,
    )
    (project / ".venv").mkdir()
    (project / ".venv/developer-sentinel").write_text("developer environment is separate\n")
    source = yaml.safe_load((workflow.package / "goon.yml").read_text())
    package = workflow.selection.parent / "goons/general/native-training-timeout"
    package.mkdir(parents=True)
    (package / "README.md").write_text(
        "# general/native-training-timeout\n\nNative UV and timeout own execution.\n"
    )
    step = dict(source["steps"][0])
    step["run"] = "uv run timeout --signal=TERM --kill-after=2s 8s " + step["run"]
    step["artifacts"] = [{**artifact, "retention": "evidence"} for artifact in step["artifacts"]]
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "uv": {"project": ".", "lock_mode": "frozen"},
                "inputs": source["inputs"],
                "steps": [step],
            },
            sort_keys=False,
        )
    )
    workflow.selection.with_name("plan.yml").write_text(
        "goons:\n- import: general/native-training-timeout\n  from: goons\n"
    )
    selected = yaml.safe_load(workflow.selection.read_text())
    selected["target"] = {"repository": str(project)}
    workflow.selection.write_text(yaml.safe_dump(selected, sort_keys=False))
    return replace(workflow, repository=project)


def test_relocated_training_cli_reopens_reproducible_and_failed_retained_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _workflow(tmp_path)
    native_lock = (workflow.package / "uv.lock").read_bytes()
    workflow.prepare()
    original_lock = workflow.selection.with_name("goon.lock").read_bytes()
    first, _ = workflow.cli("run", str(workflow.selection))
    first_id = first["fleet_run_id"]
    assert first["status"] == "COMPLETED" and first["outcome"] == "SUCCESS"
    first_step = _training_step(workflow.tape(first_id))
    assert first_step["attempt_count"] == 1
    assert first_step["project_source"]["content_sha256"]
    assert first_step["uv"]["lock_mode"] == "frozen"
    assert first_step["settlement"]["outcome"] == "SUCCESS"
    assert first_step["settlement"]["cleanup"] == "VERIFIED"
    first_exports = workflow.exported(first_id, "first")
    first_metrics = first_exports[METRICS][1]
    assert first_metrics["training_rounds"] == first_metrics["completed_training_rounds"] == 3
    assert first_metrics["random_seed"] == 42
    assert not (workflow.package / "ml-output").exists()
    assert not (workflow.repository / "ml-output").exists()

    repeated, _ = workflow.cli("run", str(workflow.selection))
    assert repeated["fleet_run_id"] != first_id and repeated["outcome"] == "SUCCESS"
    repeated_exports = workflow.exported(repeated["fleet_run_id"], "repeated")
    assert repeated_exports == first_exports
    assert workflow.selection.with_name("goon.lock").read_bytes() == original_lock

    workflow.prepare(random_seed=7)
    assert workflow.selection.with_name("goon.lock").read_bytes() != original_lock
    changed, _ = workflow.cli("run", str(workflow.selection))
    changed_exports = workflow.exported(changed["fleet_run_id"], "changed")
    assert changed["outcome"] == "SUCCESS" and changed["fleet_run_id"] != first_id
    assert changed_exports[METRICS][1]["random_seed"] == 7
    assert changed_exports[MODEL][0] != first_exports[MODEL][0]
    assert workflow.cli("runs", "show", first_id)[0]["outcome"] == "SUCCESS"
    assert workflow.exported(first_id, "reopened") == first_exports

    workflow.prepare(random_seed=42, minimum_accuracy_percent=100)
    failed, _ = workflow.cli("run", str(workflow.selection), expected=1)
    failed_step = _training_step(workflow.tape(failed["fleet_run_id"]))
    assert failed["status"] == "COMPLETED" and failed["outcome"] == "ATTENTION_REQUIRED"
    assert failed_step["attempt_count"] == 1 and failed_step["exit_code"] == 1
    assert failed_step["settlement"]["outcome"] == "FAILED"
    failed_exports = workflow.exported(failed["fleet_run_id"], "failed")
    assert failed_exports[METRICS][1]["accuracy"] < 1.0
    assert failed_exports[MODEL][0] == first_exports[MODEL][0]
    assert (workflow.package / "uv.lock").read_bytes() == native_lock
    assert (
        workflow.package / ".venv/developer-sentinel"
    ).read_text() == "developer environment is separate\n"
    assert not (workflow.package / "ml-output").exists()
    assert not (workflow.repository / "ml-output").exists()
    with capsys.disabled():
        print(
            "HUG82_TRAINING_EVIDENCE "
            + json.dumps(
                {
                    "scope": "short uncapped product Runs; no production resource-cap qualification",
                    "runs": [
                        first_id,
                        repeated["fleet_run_id"],
                        changed["fleet_run_id"],
                        failed["fleet_run_id"],
                    ],
                    "training_rounds": 3,
                    "project_source_sha256": first_step["project_source"]["content_sha256"],
                    "native_uv_lock_sha256": hashlib.sha256(native_lock).hexdigest(),
                    "original_model_sha256": first_exports[MODEL][0],
                    "changed_seed_model_sha256": changed_exports[MODEL][0],
                    "failed_threshold_model_sha256": failed_exports[MODEL][0],
                    "repeated_original_exports_equal": repeated_exports == first_exports,
                    "failed_threshold_outcome": failed["outcome"],
                },
                sort_keys=True,
            ),
            flush=True,
        )


def test_invalid_captured_training_cli_fails_before_model_artifact_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _workflow(tmp_path)
    data = workflow.package / "src/iris_train/data"
    data.mkdir()
    (data / "training.json").write_text('{"data": [], "target": []}\n')
    workflow.prepare(dataset="captured")
    result, _ = workflow.cli("run", str(workflow.selection), expected=1)
    step = _training_step(workflow.tape(result["fleet_run_id"]))
    assert result["status"] == "COMPLETED" and result["outcome"] == "ATTENTION_REQUIRED"
    assert step["attempt_count"] == 1 and step["exit_code"] == 2
    assert step["settlement"]["outcome"] == "FAILED"
    assert all(item["status"] == "MISSING" for item in step["artifacts"])
    assert not (workflow.package / "ml-output").exists()
    assert not (workflow.repository / "ml-output").exists()
    with capsys.disabled():
        print(
            "HUG82_TRAINING_EVIDENCE "
            + json.dumps(
                {
                    "scope": "invalid captured data refused before model creation; no production resource-cap qualification",
                    "run_id": result["fleet_run_id"],
                    "run_status": result["status"],
                    "run_outcome": result["outcome"],
                    "step_exit_code": step["exit_code"],
                    "project_source_sha256": step["project_source"]["content_sha256"],
                    "native_uv_lock_sha256": hashlib.sha256(
                        (workflow.package / "uv.lock").read_bytes()
                    ).hexdigest(),
                    "uv_group": step["uv"].get("group"),
                    "artifacts": step["artifacts"],
                },
                sort_keys=True,
            ),
            flush=True,
        )


def _active_resource_processes(
    tape: dict[str, Any], run_id: str, resources: CicdResourcePolicy
) -> tuple[dict[str, Any], list[int]]:
    envelope = tape["execution"]["resource_envelope"]
    assert envelope["fleet_run_id"] == tape["fleet_run_id"] == run_id
    assert envelope["resources"] == resources.model_dump(exclude_none=True)
    observed = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            envelope["unit_name"],
            "--property=InvocationID",
            "--property=ControlGroup",
            "--property=OOMPolicy",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    facts = dict(line.split("=", 1) for line in observed.stdout.splitlines())
    assert facts["InvocationID"] == envelope["invocation_id"]
    assert facts["OOMPolicy"] == envelope["oom_policy"] == "kill"
    root = Path("/sys/fs/cgroup")
    cgroup = root / facts["ControlGroup"].lstrip("/")
    assert cgroup.is_relative_to(root)
    descriptor = os.open(cgroup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        assert (metadata.st_dev, metadata.st_ino) == (
            envelope["cgroup_device"],
            envelope["cgroup_inode"],
        )

        def read(name: str) -> str:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            with os.fdopen(fd, encoding="ascii") as stream:
                value = stream.read(8193)
            assert len(value) <= 8192
            return value.strip()

        assert read("cpu.max") == envelope["cpu_max"]
        assert read("memory.max") == str(envelope["memory_max_bytes"])
        assert read("memory.swap.max") == "0"
        assert read("pids.max") == str(envelope["pids_max"])
        assert read("memory.oom.group") == "1"
        quota, period = map(int, envelope["cpu_max"].split())
        assert quota <= 2 * period
        assert envelope["memory_max_bytes"] <= 1024 * 1024 * 1024
        assert envelope["pids_max"] <= 256
        pids = [int(value) for value in read("cgroup.procs").splitlines()]
        assert envelope["process_birth"]["pid"] in pids
        cli_birth = ProcessBirthIdentity.from_payload(envelope["process_birth"])
        handles = []
        try:
            for pid in pids:
                try:
                    handle = os.pidfd_open(pid)
                except ProcessLookupError:
                    if pid == cli_birth.pid:
                        raise
                    continue
                handles.append(handle)
                if pid == cli_birth.pid:
                    assert observe_process_birth(pid) == cli_birth
            return envelope, handles
        except BaseException:
            for handle in handles:
                os.close(handle)
            raise
    finally:
        os.close(descriptor)


@pytest.mark.no_resume
@pytest.mark.native_resources
@pytest.mark.skipif(
    not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"),
    reason="native process-identity handles required",
)
@pytest.mark.parametrize("stop", ("SIGINT", "SIGTERM", "timeout", "complete"))
def test_budgeted_training_progress_precedes_signal_or_deadline_settlement(
    tmp_path: Path, stop: str, capsys: pytest.CaptureFixture[str]
) -> None:
    resources = CicdResourcePolicy(cpu_millis=2000, memory_mib=1024, max_processes=256)
    require_native_run_resource_context(resources)
    workflow = _workflow(tmp_path, resources=resources)
    workflow.prepare()
    control, _ = workflow.cli("run", str(workflow.selection))
    assert control["outcome"] == "SUCCESS"
    control_output = workflow.cli("runs", "outputs", control["fleet_run_id"])[0]
    if stop == "timeout":
        workflow = _native_timeout_workflow(workflow)
    assert not (workflow.repository / "ml-output").exists()
    workflow.prepare(training_rounds=10000)
    run_id: str | None = None
    handles: list[int] = []
    envelope: dict[str, Any] | None = None
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        invocation_started = time.monotonic()
        process = subprocess.Popen(
            [str(workflow.executable), "run", str(workflow.selection), "--json"],
            cwd=tmp_path,
            env=workflow.environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 40
            while True:
                history = workflow.cli("runs", "list")[0]["items"]
                running = [row for row in history if row["status"] == "RUNNING"]
                if running:
                    assert len(running) == 1
                    run_id = running[0]["fleet_run_id"]
                    break
                assert process.poll() is None
                assert time.monotonic() < deadline, "original Run did not reach admission"
            envelope, handles = _active_resource_processes(workflow.tape(run_id), run_id, resources)
            while True:
                stderr.seek(0)
                progress = stderr.read(128 * 1024 + 1)
                assert len(progress) <= 128 * 1024
                if (
                    b"IRIS_TRAINING_STARTED rounds=10000" in progress
                    and b"IRIS_TRAINING_PROGRESS completed=1 total=10000" in progress
                ):
                    break
                assert process.poll() is None, progress[-8192:]
                assert time.monotonic() < deadline, progress[-8192:]
                time.sleep(0.05)
            assert process.poll() is None
            assert stdout.tell() == 0
            if stop in {"SIGINT", "SIGTERM"}:
                process.send_signal(getattr(signal, stop))
            expected_exit = {"SIGINT": 130, "SIGTERM": 143, "timeout": 1, "complete": 0}[stop]
            assert process.wait(timeout=150 if stop == "complete" else 40) == expected_exit
            invocation_elapsed = time.monotonic() - invocation_started
            stdout.seek(0)
            payload = stdout.read(512 * 1024 + 1)
            assert len(payload) <= 512 * 1024
            result = json.loads(payload)
            assert result["fleet_run_id"] == run_id
            assert result["resource_observation_persistence"] == "RECORDED"
            for handle in handles:
                with pytest.raises(ProcessLookupError):
                    signal.pidfd_send_signal(handle, 0)
            retired = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    envelope["unit_name"],
                    "--property=InvocationID",
                    "--property=ActiveState",
                    "--property=ControlGroup",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            retired_facts = dict(line.split("=", 1) for line in retired.stdout.splitlines())
            assert retired_facts["InvocationID"] in ("", envelope["invocation_id"])
            assert retired_facts["ActiveState"] in ("inactive", "failed")
            assert retired_facts["ControlGroup"] == ""
            tape = workflow.tape(run_id)
            step = _training_step(tape)
            assert step["settlement"]["cleanup"] == "VERIFIED"
            assert (
                step["settlement"]["outcome"]
                == {
                    "timeout": "TIMED_OUT",
                    "complete": "SUCCESS",
                    "SIGINT": "CANCELLED",
                    "SIGTERM": "CANCELLED",
                }[stop]
            )
            assert step["attempt_count"] == (1 if stop in {"timeout", "complete"} else 0)
            if stop == "timeout":
                assert step["exit_code"] == 124
                assert step["settlement"]["observed_returncode"] == 124
                assert step["settlement"]["reason"] == "DEADLINE_EXPIRED"
                supervisor = step["attempts"][0].get("supervisor")
                if supervisor is not None:
                    assert supervisor["cleanup_reason"] == "none"
                assert step["project_source"] is None
                assert step["uv"]["project"] == "."
            status = workflow.cli("runs", "show", run_id)[0]
            assert status["status"] == (
                "COMPLETED" if stop in {"timeout", "complete"} else "CANCELLED"
            )
            assert (
                status["outcome"]
                == {
                    "timeout": "ATTENTION_REQUIRED",
                    "complete": "SUCCESS",
                    "SIGINT": "CANCELLED",
                    "SIGTERM": "CANCELLED",
                }[stop]
            )
            final = tape["resource_observation"]
            assert final["unit_name"] == envelope["unit_name"]
            assert final["invocation_id"] == envelope["invocation_id"]
            assert final["cleanup"] == "VERIFIED"
            exported = None
            if stop == "complete":
                exported = workflow.exported(run_id, "complete")
                assert exported[METRICS][1]["completed_training_rounds"] == 10000
                assert exported[METRICS][1]["training_rounds"] == 10000
                assert step["project_source"]["content_sha256"]
                assert step["uv"].get("group") is None
            else:
                assert not any(item["status"] == "PRESENT" for item in step["artifacts"])
            assert not (workflow.package / "ml-output").exists()
            assert not (workflow.repository / "ml-output").exists()
            assert (
                workflow.cli("runs", "outputs", control["fleet_run_id"])[0]["repositories"]
                == control_output["repositories"]
            )
            with capsys.disabled():
                print(
                    "HUG82_TRAINING_EVIDENCE "
                    + json.dumps(
                        {
                            "scope": "actual budgeted native Iris training through public CLI and claimed worker",
                            "case": stop,
                            "run_id": run_id,
                            "control_run_id": control["fleet_run_id"],
                            "requested_rounds": 10000,
                            "invocation_wall_seconds": invocation_elapsed,
                            "invocation_wall_scope": "whole waited CLI invocation including admission and native preparation",
                            "recorded_step_duration_ms": step["duration_ms"],
                            "recorded_step_duration_scope": "recorded native command or cancellation settlement duration, not isolated fit time",
                            "project_source_sha256": step["project_source"]["content_sha256"]
                            if step["project_source"] is not None
                            else None,
                            "repository_head_sha": tape["repositories"][0]["fingerprint"][
                                "repository_head_sha"
                            ],
                            "execution_lane": "repository-native UV with native timeout"
                            if stop == "timeout"
                            else "captured Python Goon package",
                            "deadline_owner": "native timeout utility; canonical reserved exit124 settles TIMED_OUT, not expiry of the backend3600s command timer"
                            if stop == "timeout"
                            else "normal backend command default",
                            "first_training_fit_observed_before_settlement": True,
                            "native_uv_lock_sha256": hashlib.sha256(
                                (
                                    (workflow.repository if stop == "timeout" else workflow.package)
                                    / "uv.lock"
                                ).read_bytes()
                            ).hexdigest(),
                            "uv_group": step["uv"].get("group"),
                            "run_status": status["status"],
                            "run_outcome": status["outcome"],
                            "step_settlement": step["settlement"],
                            "resource_envelope": {
                                key: envelope[key]
                                for key in (
                                    "unit_name",
                                    "invocation_id",
                                    "resources",
                                    "cpu_max",
                                    "memory_max_bytes",
                                    "memory_swap_max_bytes",
                                    "pids_max",
                                    "oom_policy",
                                    "memory_oom_group",
                                )
                            },
                            "resource_observation": final,
                            "sampled_processes_retired": len(handles),
                            "model_sha256": exported[MODEL][0] if exported is not None else None,
                            "metrics_sha256": exported[METRICS][0]
                            if exported is not None
                            else None,
                            "completed_rounds": exported[METRICS][1]["completed_training_rounds"]
                            if exported is not None
                            else None,
                            "strict_custody_or_other_platform_qualification": "NOT_CLAIMED",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
        finally:
            primary = sys.exc_info()[1]
            cleanup_errors: list[str] = []
            try:
                if envelope is not None:
                    try:
                        current = subprocess.run(
                            [
                                "systemctl",
                                "--user",
                                "show",
                                envelope["unit_name"],
                                "--property=InvocationID",
                                "--property=LoadState",
                                "--property=ActiveState",
                                "--property=ControlGroup",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=False,
                        )
                        facts = dict(line.split("=", 1) for line in current.stdout.splitlines())
                        if current.returncode != 0:
                            cleanup_errors.append("owned native unit observation failed")
                        elif facts.get("InvocationID") == envelope["invocation_id"]:
                            stopped = subprocess.run(
                                ["systemctl", "--user", "stop", envelope["unit_name"]],
                                capture_output=True,
                                text=True,
                                timeout=15,
                                check=False,
                            )
                            if stopped.returncode != 0:
                                cleanup_errors.append("owned native unit stop failed")
                        elif not (
                            facts.get("InvocationID") == ""
                            and facts.get("ControlGroup") == ""
                            and facts.get("LoadState") == "not-found"
                            and facts.get("ActiveState") == "inactive"
                        ):
                            cleanup_errors.append(
                                "native unit no longer matches the owned invocation"
                            )
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        cleanup_errors.append(
                            f"owned native unit cleanup unavailable: {type(error).__name__}"
                        )
            finally:
                try:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                        cleanup_errors.append("original budgeted CLI exceeded its cleanup bound")
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(
                        f"original CLI cleanup unavailable: {type(error).__name__}"
                    )
                finally:
                    for handle in handles:
                        try:
                            os.close(handle)
                        except OSError as error:
                            cleanup_errors.append(
                                f"native PID handle close failed: {type(error).__name__}"
                            )
            if cleanup_errors:
                message = "; ".join(cleanup_errors)
                if primary is not None:
                    primary.add_note(message)
                else:
                    pytest.fail(message)
