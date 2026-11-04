from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryFile
from uuid import uuid4

import pytest

from oh_goon.repository_tooling import _native_test_worker as owner

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci_native_runtime.sh"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native runtime owner")


def _environment(profile: str | None, **selected: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "OH_GOON_NATIVE_QUALIFICATION_PROFILE",
        "OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH",
    ):
        environment.pop(name, None)
    environment.update(RUNNER_ENVIRONMENT="local", OH_GOON_TEST_CPUS="0")
    if profile is not None:
        environment["OH_GOON_NATIVE_QUALIFICATION_PROFILE"] = profile
    environment.update(selected)
    return environment


@pytest.mark.parametrize(
    "profile,arguments",
    [
        ("unknown-profile", ()),
        (None, ("--native-resources-needed", "true", "--")),
        ("shared-user-manager", ("--native-resources-needed", "TRUE", "--")),
    ],
)
def test_invalid_native_preparation_refuses_before_original_command(
    tmp_path: Path, profile: str | None, arguments: tuple[str, ...]
) -> None:
    marker = tmp_path / "command-ran"
    result = subprocess.run(
        ["bash", str(SCRIPT), *arguments, "/usr/bin/touch", str(marker)],
        env=_environment(profile),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert not marker.exists()


def test_local_default_does_not_allocate_a_qualification_worker(tmp_path: Path) -> None:
    marker = tmp_path / "command-ran"
    result = subprocess.run(
        ["bash", str(SCRIPT), "/usr/bin/touch", str(marker)],
        env=_environment(None),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1 and not marker.exists()


@pytest.mark.parametrize("selector", ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"))
def test_receiver_refuses_manager_environment_drift_before_acknowledgement_or_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    selected_environment = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/selected/native-bus",
        "XDG_RUNTIME_DIR": str(tmp_path / "selected-runtime"),
    }
    for name, value in selected_environment.items():
        monkeypatch.setenv(name, value)
    executable = Path(sys.executable).resolve()
    authority = owner._WorkerAuthority(
        profile="shared-user-manager",
        unit=f"oh-goon-native-worker-{uuid4().hex}.scope",
        root=ROOT,
        python=Path(sys.executable).absolute(),
        systemd_run=executable,
        systemd_run_sha256=owner._digest(executable),
        systemctl=executable,
        systemctl_sha256=owner._digest(executable),
        manager_environment=tuple(selected_environment.items()),
        cpus=0,
        project_sha256=owner._digest(ROOT / "backend/pyproject.toml"),
        lock_sha256=owner._digest(ROOT / "backend/uv.lock"),
        caller_source_revision=None,
    )
    packet, acknowledgement = tmp_path / "authority.json", tmp_path / "acknowledged.json"
    packet.write_text(authority.model_dump_json())
    packet.chmod(0o600)
    marker = tmp_path / "command-ran"
    monkeypatch.setenv(selector, "changed-after-selection")

    with pytest.raises(ValueError, match="changed its selected manager environment"):
        owner._enter(packet, acknowledgement, ("/usr/bin/touch", str(marker)))

    assert not acknowledgement.exists() and not marker.exists()


def test_selected_plan_without_native_members_keeps_original_context(
    tmp_path: Path,
) -> None:
    report = tmp_path / "context.json"
    original = Path("/proc/self/cgroup").read_text()
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--native-resources-needed",
            "false",
            "--",
            "/usr/bin/cat",
            "/proc/self/cgroup",
        ],
        env=_environment(
            "shared-user-manager", OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH=str(report)
        ),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.decode() == original
    assert not report.exists()


@pytest.mark.process
@pytest.mark.native_resources
@pytest.mark.no_resume
def test_explicit_worker_preserves_literal_arguments_output_and_nonzero_result(
    tmp_path: Path,
) -> None:
    report = tmp_path / "context.json"
    original = Path("/proc/self/cgroup").read_text()
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            sys.executable,
            "-I",
            "-c",
            "import sys; print(sys.argv[1]); raise SystemExit(17)",
            "$NATIVE_WORKER_LITERAL",
        ],
        env=_environment(
            "shared-user-manager",
            NATIVE_WORKER_LITERAL="must-not-be-expanded",
            OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH=str(report),
        ),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 17, result.stderr.decode(errors="replace")
    assert result.stdout == b"$NATIVE_WORKER_LITERAL\n"
    assert Path("/proc/self/cgroup").read_text() == original
    context = json.loads(report.read_text())
    assert context["profile"] == "shared-user-manager"
    assert context["unit"] in context["cgroup"]
    assert context["shared_ancestors"][0]["path"] == str(Path(context["cgroup"]).parent)
    observed = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            context["unit"],
            "--property=LoadState",
            "--value",
        ],
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert observed.stdout.strip() == b"not-found"


@pytest.mark.process
@pytest.mark.native_resources
@pytest.mark.no_resume
def test_configured_cpu_ceiling_is_inherited_or_refused_without_work(
    tmp_path: Path,
) -> None:
    report, marker = tmp_path / "context.json", tmp_path / "command-ran"
    result = subprocess.run(
        ["bash", str(SCRIPT), "/usr/bin/touch", str(marker)],
        env=_environment(
            "shared-user-manager",
            OH_GOON_TEST_CPUS="1",
            OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH=str(report),
        ),
        capture_output=True,
        timeout=30,
        check=False,
    )
    context = json.loads(report.read_text())
    assert context["configured_cpus"] == 1
    shared_cpu_limits = [
        row["controls"]["cpu.max"].split()
        for row in context["shared_ancestors"]
        if row["controls"].get("cpu.max", "max").split()[0] != "max"
    ]
    bounded = any(int(quota) <= int(period) for quota, period in shared_cpu_limits)
    assert result.returncode == (0 if bounded else 3), result.stderr.decode(errors="replace")
    assert marker.exists() is bounded


@pytest.mark.process
@pytest.mark.native_resources
@pytest.mark.no_resume
def test_preexisting_unit_name_is_not_owned_by_a_failed_new_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonce = uuid4()
    unit = f"oh-goon-native-worker-{nonce.hex}.scope"
    ready, forbidden = tmp_path / "foreign-ready", tmp_path / "new-command-ran"
    with TemporaryFile() as output:
        foreign = subprocess.Popen(
            [
                "systemd-run",
                "--user",
                "--scope",
                "--collect",
                "--quiet",
                "--slice=app.slice",
                "--unit=" + unit,
                "--property=TasksMax=infinity",
                "--",
                sys.executable,
                "-I",
                "-c",
                "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(120)",
                str(ready),
            ],
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and foreign.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists()
            observed = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "--property=InvocationID",
                    "--value",
                ],
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout
            monkeypatch.setattr(owner, "uuid4", lambda: nonce)

            status = owner.launch(ROOT, 0, None, ("/usr/bin/touch", str(forbidden)))

            assert status != 0 and not forbidden.exists()
            assert foreign.poll() is None
            current = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "--property=InvocationID",
                    "--value",
                ],
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout
            assert current == observed and current.strip()
        finally:
            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            foreign.wait(timeout=10)


@pytest.mark.process
@pytest.mark.native_resources
@pytest.mark.no_resume
def test_cancelled_worker_closes_its_scope_and_a_detached_child(tmp_path: Path) -> None:
    report, ready = tmp_path / "context.json", tmp_path / "child.json"
    original = Path("/proc/self/cgroup").read_text()
    detached = (
        "import json,os,pathlib,signal,sys,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()})); "
        "time.sleep(120)"
    )
    program = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-I','-c',{detached!r},sys.argv[1]],"
        "start_new_session=True); "
        "time.sleep(120)"
    )
    child_handle = None
    with TemporaryFile(mode="w+b") as stdout, TemporaryFile(mode="w+b") as stderr:
        process = subprocess.Popen(
            ["bash", str(SCRIPT), sys.executable, "-I", "-c", program, str(ready)],
            env=_environment(
                "shared-user-manager",
                OH_GOON_NATIVE_QUALIFICATION_REPORT_PATH=str(report),
            ),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), "Native worker never reached its original command"
            child_handle = os.pidfd_open(json.loads(ready.read_text())["pid"])
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=30) == 143
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    signal.pidfd_send_signal(child_handle, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                pytest.fail("Detached child survived native worker cleanup")
            assert Path("/proc/self/cgroup").read_text() == original
            context = json.loads(report.read_text())
            observed = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    context["unit"],
                    "--property=LoadState",
                    "--value",
                ],
                capture_output=True,
                timeout=10,
                check=True,
            )
            assert observed.stdout.strip() == b"not-found"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if report.exists():
                context = json.loads(report.read_text())
                subprocess.run(
                    ["systemctl", "--user", "stop", context["unit"]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            if child_handle is not None:
                try:
                    signal.pidfd_send_signal(child_handle, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.close(child_handle)
