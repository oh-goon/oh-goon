from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandOutputObserver,
    CommandResult,
    RuntimeAuthorityReceipt,
    run_command,
)
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.content_snapshot import replace_tree_from_tar_gz_stream, verify_snapshot
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    _RUNNER_PROTOCOL,
    OpenSandboxSettings,
)
from oh_goon.integrations.fleet_plan_process_isolation import (
    MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
    IsolatedProcessRequest,
)
from oh_goon.integrations.fleet_plan_runtime_authority import require_runtime_authority_observation

_RUNNER_COMMAND = ("/opt/oh-goon/attempt-runner", "/fleet/request.json", "/fleet/result.json")
_API_TIMEOUT_SECONDS = 10.0
_REMOTE_INPUT_ROOT = "/fleet/input"
_REMOTE_OUTPUT_ROOT = "/fleet/output"


class OpenSandboxCliTransport:
    """Provider transport with Oh-Goon-owned lifecycle policy and external osb effects.

    OpenSandbox is an execution provider, not Python dependency authority. Lifecycle
    creation is authored directly against the published API so deny-all egress and
    exact CPU/memory limits remain visible Oh-Goon policy. The provider CLI is an
    executor-side admitted tool used only for file/command/cleanup effects.
    """

    def require_available(self, settings: OpenSandboxSettings) -> None:
        settings.validate()
        _probe_server(settings)
        result = _run_osb(settings, "--version", timeout=_API_TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise FleetPlanInvalid("Configured OpenSandbox osb CLI is not executable/healthy")

    def execute(
        self,
        settings: OpenSandboxSettings,
        request: IsolatedProcessRequest,
        *,
        expected_runtime_authority: RuntimeAuthorityReceipt,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
    ) -> CommandResult:
        if cancel_requested is not None and cancel_requested():
            raise CommandCancelledError(
                "Fleet OpenSandbox attempt was cancelled before sandbox creation"
            )
        resources = request.attempt.resources
        if resources.max_processes is not None:
            raise FleetPlanInvalid(
                "OpenSandbox backend does not prove Fleet max_processes enforcement"
            )
        sandbox_id = _create_sandbox(settings, resources=resources, timeout=timeout)
        try:
            return _execute_open_sandbox_attempt(
                settings,
                request,
                sandbox_id=sandbox_id,
                expected_runtime_authority=expected_runtime_authority,
                timeout=timeout,
                cancel_requested=cancel_requested,
                stream_output=stream_output,
            )
        finally:
            write_remote_resource_evidence(request.attempt.paths.root, resources)
            _destroy_sandbox(settings, sandbox_id)


def _execute_open_sandbox_attempt(
    settings: OpenSandboxSettings,
    request: IsolatedProcessRequest,
    *,
    sandbox_id: str,
    expected_runtime_authority: RuntimeAuthorityReceipt,
    timeout: float | None,
    cancel_requested: Callable[[], bool] | None,
    stream_output: CommandOutputObserver | None,
) -> CommandResult:
    with tempfile.TemporaryDirectory(prefix="oh-goon-opensandbox-") as temp:
        local = Path(temp)
        os.chmod(local, 0o700)
        input_root = local / "input"
        input_root.mkdir(mode=0o700)
        request_file = local / "request.json"
        result_file = local / "result.json"
        request_file.write_text(
            json.dumps(
                _request_payload(request, timeout=timeout, staging_root=input_root),
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.chmod(request_file, 0o600)
        _require_cli_success(
            _run_osb(
                settings,
                "command",
                "run",
                sandbox_id,
                "-o",
                "raw",
                "--",
                "mkdir",
                "-p",
                "/fleet",
                _REMOTE_INPUT_ROOT,
                _REMOTE_OUTPUT_ROOT,
                timeout=_command_timeout(timeout),
                cancel_requested=cancel_requested,
            ),
            "create Fleet sandbox work directory",
        )
        _upload_staged_inputs(
            settings, sandbox_id, input_root, timeout=timeout, cancel_requested=cancel_requested
        )
        _require_cli_success(
            _run_osb(
                settings,
                "file",
                "upload",
                sandbox_id,
                str(request_file),
                "/fleet/request.json",
                "-o",
                "json",
                timeout=_command_timeout(timeout),
                cancel_requested=cancel_requested,
            ),
            "upload Fleet sandbox request",
        )
        if cancel_requested is not None and cancel_requested():
            raise CommandCancelledError("Fleet OpenSandbox attempt cancelled before process launch")
        execution = _run_osb(
            settings,
            "command",
            "run",
            sandbox_id,
            "-o",
            "raw",
            "--",
            *_RUNNER_COMMAND,
            timeout=timeout,
            cancel_requested=cancel_requested,
        )
        _require_cli_success(execution, "run Fleet sandbox helper")
        _require_cli_success(
            _run_osb(
                settings,
                "file",
                "download",
                sandbox_id,
                "/fleet/result.json",
                str(result_file),
                "-o",
                "json",
                timeout=_command_timeout(timeout),
                cancel_requested=cancel_requested,
            ),
            "download Fleet sandbox result",
        )
        result = _read_remote_result(result_file)
        result_archives = _download_result_archives(
            settings,
            sandbox_id,
            request,
            result,
            local,
            timeout=timeout,
            cancel_requested=cancel_requested,
        )
        return _command_result_from_remote(
            result,
            request,
            execution,
            result_archives=result_archives,
            expected_runtime_authority=expected_runtime_authority,
            stream_output=stream_output,
        )


def _read_remote_result(result_file: Path) -> dict[str, object]:
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetPlanInvalid("OpenSandbox Fleet runner result is not valid JSON") from exc
    if not isinstance(result, dict):
        raise FleetPlanInvalid("OpenSandbox Fleet runner result must be a JSON object")
    return result


def _command_result_from_remote(
    result: dict[str, object],
    request: IsolatedProcessRequest,
    execution: CommandResult,
    *,
    result_archives: dict[str, Path],
    expected_runtime_authority: RuntimeAuthorityReceipt,
    stream_output: CommandOutputObserver | None,
) -> CommandResult:
    observed = result.get("runtime_authority")
    if not isinstance(observed, dict):
        raise FleetPlanInvalid(
            "OpenSandbox Fleet runner result is missing runtime authority observation"
        )
    require_runtime_authority_observation(expected_runtime_authority, observed)
    _restore_result_archives(request, result, result_archives)
    stdout = str(result.get("stdout", ""))
    stderr = str(result.get("stderr", ""))
    _observe_returned_output(stream_output, stdout, stderr)
    try:
        returncode = int(result["returncode"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FleetPlanInvalid("OpenSandbox Fleet runner result has no integer returncode") from exc
    return CommandResult(
        argv=request.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=execution.duration_ms,
        supervisor=execution.supervisor,
        runtime_authority=expected_runtime_authority,
    )


def _observe_returned_output(
    observer: CommandOutputObserver | None, stdout: str, stderr: str
) -> None:
    if observer is None:
        return
    if stdout:
        observer("stdout", stdout)
    if stderr:
        observer("stderr", stderr)


def _opensandbox_resource(policy: CicdResourcePolicy) -> dict[str, str]:
    resource: dict[str, str] = {}
    if policy.cpu_millis is not None:
        resource["cpu"] = (
            str(policy.cpu_millis // 1000)
            if policy.cpu_millis % 1000 == 0
            else f"{policy.cpu_millis}m"
        )
    if policy.memory_mib is not None:
        resource["memory"] = f"{policy.memory_mib}Mi"
    return resource


def _api_base(settings: OpenSandboxSettings) -> str:
    return f"{settings.protocol}://{settings.domain}/v1"


def _api_headers(settings: OpenSandboxSettings) -> dict[str, str]:
    return {"OPEN-SANDBOX-API-KEY": settings.api_key, "Content-Type": "application/json"}


def _httpx2() -> Any:
    try:
        import httpx2
    except ImportError as exc:  # pragma: no cover - dependency-complete surface owns this
        raise FleetPlanInvalid(
            "OpenSandbox lifecycle transport requires Oh-Goon's httpx2 runtime dependency"
        ) from exc
    return httpx2


def _probe_server(settings: OpenSandboxSettings) -> None:
    httpx2 = _httpx2()
    try:
        response = httpx2.get(
            f"{_api_base(settings)}/sandboxes",
            params={"page": 1, "pageSize": 1},
            headers=_api_headers(settings),
            timeout=_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:
        raise FleetPlanInvalid(
            "Configured OpenSandbox server is not reachable/authenticated before RUN_ADMITTED"
        ) from exc


def _create_sandbox(
    settings: OpenSandboxSettings,
    *,
    resources: CicdResourcePolicy,
    timeout: float | None,
) -> str:
    payload: dict[str, object] = {
        "image": {"uri": settings.image},
        "entrypoint": ["tail", "-f", "/dev/null"],
        "networkPolicy": {"defaultAction": "deny", "egress": []},
        "resourceLimits": _opensandbox_resource(resources),
        "metadata": {
            "owner": "oh-goon",
            "runtime-attestation": settings.runtime_attestation_sha256,
        },
    }
    if timeout is not None:
        payload["timeout"] = max(60, int(timeout) + 30)
    httpx2 = _httpx2()
    try:
        response = httpx2.post(
            f"{_api_base(settings)}/sandboxes",
            headers=_api_headers(settings),
            json=payload,
            timeout=_API_TIMEOUT_SECONDS,
        )
        if response.status_code != 202:
            raise FleetPlanInvalid(
                f"OpenSandbox create returned HTTP {response.status_code}; expected 202"
            )
        body = response.json()
    except FleetPlanInvalid:
        raise
    except Exception as exc:
        raise FleetPlanInvalid("OpenSandbox sandbox creation failed") from exc
    if not isinstance(body, dict) or not isinstance(body.get("id"), str) or not body["id"].strip():
        raise FleetPlanInvalid("OpenSandbox create response is missing sandbox id")
    return body["id"].strip()


def _destroy_sandbox(settings: OpenSandboxSettings, sandbox_id: str) -> None:
    # Cleanup is best effort only after durable resource evidence is emitted. Prefer the
    # lifecycle API so cleanup does not depend on CLI output format/version.
    try:
        httpx2 = _httpx2()
        response = httpx2.delete(
            f"{_api_base(settings)}/sandboxes/{sandbox_id}",
            headers=_api_headers(settings),
            timeout=_API_TIMEOUT_SECONDS,
        )
        if response.status_code not in {204, 404}:
            _run_osb(
                settings, "sandbox", "kill", sandbox_id, "-o", "json", timeout=_API_TIMEOUT_SECONDS
            )
    except Exception:
        try:
            _run_osb(
                settings, "sandbox", "kill", sandbox_id, "-o", "json", timeout=_API_TIMEOUT_SECONDS
            )
        except Exception:
            pass


def _osb_environment(settings: OpenSandboxSettings) -> dict[str, str]:
    env = dict(os.environ)
    env["OPEN_SANDBOX_API_KEY"] = settings.api_key
    env["OPEN_SANDBOX_DOMAIN"] = settings.domain
    return env


def _run_osb(
    settings: OpenSandboxSettings,
    *args: str,
    timeout: float | None,
    cancel_requested: Callable[[], bool] | None = None,
) -> CommandResult:
    # Secret authority is environment-only. Never append api_key to argv.
    return run_command(
        settings.cli_executable,
        "--domain",
        settings.domain,
        "--protocol",
        settings.protocol,
        *args,
        timeout=timeout,
        env=_osb_environment(settings),
        cancel_requested=cancel_requested,
        parent_death_signal=True,
        require_process_group_quiescence=True,
    )


def _require_cli_success(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        raise FleetPlanInvalid(
            f"OpenSandbox CLI could not {action}: exit={result.returncode}; stderr={result.stderr[-500:]}"
        )


def _command_timeout(timeout: float | None) -> float:
    return _API_TIMEOUT_SECONDS if timeout is None else max(_API_TIMEOUT_SECONDS, timeout)


def _request_payload(
    request: IsolatedProcessRequest,
    *,
    timeout: float | None,
    staging_root: Path,
) -> dict[str, object]:
    attempt = request.attempt
    launch = sandbox_process_launch(request)
    readonly: list[dict[str, object]] = []
    read_only_cache = None
    readonly_root = staging_root / "readonly"
    readonly_root.mkdir(parents=True, exist_ok=True)
    for index, binding in enumerate(attempt.sandbox_bindings):
        if binding.sandbox_path is None:
            raise FleetPlanInvalid("Managed runtime input is missing its sandbox path")
        if str(binding.sandbox_path) == "/cache":
            if read_only_cache is not None:
                raise FleetPlanInvalid(
                    "Fleet attempt cannot carry multiple read-only /cache inputs"
                )
            verify_snapshot(
                binding.snapshot_path, binding.identity, label="OpenSandbox read-only cache input"
            )
            read_only_cache = binding
            continue
        readonly.append(
            _snapshot_payload(
                binding,
                declared_path=str(binding.sandbox_path),
                staging_root=readonly_root,
                index=index,
            )
        )
    workspace_archive = staging_root / "workspace.tar.gz"
    cache_archive = staging_root / "cache.tar.gz"
    runtime_archive = staging_root / "runtime-root.tar.gz"
    _write_tree_archive(attempt.paths.workspace, workspace_archive)
    _write_tree_archive(
        read_only_cache.snapshot_path if read_only_cache is not None else attempt.paths.cache,
        cache_archive,
    )
    _write_tree_archive(attempt.runtime_root.root, runtime_archive)
    toolchain_root = staging_root / "toolchain"
    toolchain_root.mkdir(parents=True, exist_ok=True)
    toolchain: list[dict[str, object]] = []
    for index, binding in enumerate(attempt.host_tools):
        destination = toolchain_root / f"{index:03d}-{binding.name}"
        shutil.copyfile(binding.execution_snapshot_path, destination)
        toolchain.append(
            {
                "name": binding.name,
                "source": f"input/toolchain/{destination.name}",
                "mode": binding.execution_snapshot_path.stat().st_mode & 0o777,
            }
        )
    return {
        "protocol": _RUNNER_PROTOCOL,
        "argv": list(launch.argv),
        "cwd": str(launch.cwd),
        "env": launch.env,
        "timeout": timeout,
        "max_output_bytes_per_stream": MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
        "workspace_archive": "input/workspace.tar.gz",
        "cache_archive": "input/cache.tar.gz",
        "cache_read_only": read_only_cache is not None,
        "runtime_root_archive": "input/runtime-root.tar.gz",
        "toolchain": toolchain,
        "readonly_inputs": readonly,
        "etc_files": {
            name: (attempt.paths.etc / name).read_text(encoding="utf-8")
            for name in ("passwd", "group", "nsswitch.conf", "hosts")
        },
        "egress": {"mode": "none"},
    }


def _snapshot_payload(
    binding: object,
    *,
    declared_path: str,
    staging_root: Path,
    index: int,
) -> dict[str, object]:
    path = binding.snapshot_path  # type: ignore[attr-defined]
    verify_snapshot(path, binding.identity, label=f"OpenSandbox read-only input {declared_path}")  # type: ignore[attr-defined]
    if path.is_dir():
        archive = staging_root / f"{index:03d}.tar.gz"
        _write_tree_archive(path, archive)
        return {
            "declared_path": declared_path,
            "kind": "tree",
            "source": f"input/readonly/{archive.name}",
        }
    source = staging_root / f"{index:03d}.file"
    shutil.copyfile(path, source)
    return {
        "declared_path": declared_path,
        "kind": "file",
        "source": f"input/readonly/{source.name}",
        "mode": path.stat().st_mode & 0o777,
    }


def _write_tree_archive(root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        path_space = RepositoryPathSpace.from_root(root)
        for path in sorted(
            root.rglob("*"),
            key=lambda item: path_space.relative(item, label="OpenSandbox archive path").as_posix(),
        ):
            relative = path_space.relative(path, label="OpenSandbox archive path")
            archive.add(path, arcname=relative.as_posix(), recursive=False)


def _restore_result_archives(
    request: IsolatedProcessRequest,
    result: dict[str, object],
    result_archives: dict[str, Path],
) -> None:
    if int(result.get("protocol", -1)) != _RUNNER_PROTOCOL:
        raise FleetPlanInvalid("OpenSandbox Fleet runner returned an incompatible protocol version")
    result_paths = [("workspace_archive", request.attempt.paths.workspace)]
    if not any(
        str(binding.sandbox_path) == "/cache" for binding in request.attempt.sandbox_bindings
    ):
        result_paths.append(("cache_archive", request.attempt.paths.cache))
    for key, destination in result_paths:
        remote = result.get(key)
        archive = result_archives.get(key)
        if not isinstance(remote, str) or archive is None:
            raise FleetPlanInvalid(f"OpenSandbox Fleet runner result is missing {key}")
        _replace_tree_from_archive(destination, archive)


def _replace_tree_from_archive(destination: Path, archive: Path) -> None:
    try:
        with archive.open("rb") as stream:
            replace_tree_from_tar_gz_stream(
                stream, destination, label="OpenSandbox Fleet runner archive"
            )
    except (FleetPlanInvalid, OSError, tarfile.TarError, ValueError) as exc:
        raise FleetPlanInvalid("OpenSandbox Fleet runner archive is invalid") from exc


def _upload_staged_inputs(
    settings: OpenSandboxSettings,
    sandbox_id: str,
    input_root: Path,
    *,
    timeout: float | None,
    cancel_requested: Callable[[], bool] | None,
) -> None:
    directories = sorted(
        {
            path.parent.relative_to(input_root).as_posix()
            for path in input_root.rglob("*")
            if path.is_file()
        }
    )
    remote_directories = [
        _REMOTE_INPUT_ROOT if value == "." else f"{_REMOTE_INPUT_ROOT}/{value}"
        for value in directories
    ]
    if remote_directories:
        _require_cli_success(
            _run_osb(
                settings,
                "command",
                "run",
                sandbox_id,
                "-o",
                "raw",
                "--",
                "mkdir",
                "-p",
                *remote_directories,
                timeout=_command_timeout(timeout),
                cancel_requested=cancel_requested,
            ),
            "create Fleet sandbox input directories",
        )
    for path in sorted(item for item in input_root.rglob("*") if item.is_file()):
        relative = path.relative_to(input_root).as_posix()
        _require_cli_success(
            _run_osb(
                settings,
                "file",
                "upload",
                sandbox_id,
                str(path),
                f"{_REMOTE_INPUT_ROOT}/{relative}",
                "-o",
                "json",
                timeout=_command_timeout(timeout),
                cancel_requested=cancel_requested,
            ),
            f"upload Fleet sandbox input {relative}",
        )


def _download_result_archives(
    settings: OpenSandboxSettings,
    sandbox_id: str,
    request: IsolatedProcessRequest,
    result: dict[str, object],
    local: Path,
    *,
    timeout: float | None,
    cancel_requested: Callable[[], bool] | None,
) -> dict[str, Path]:
    keys = ["workspace_archive"]
    if not any(
        str(binding.sandbox_path) == "/cache" for binding in request.attempt.sandbox_bindings
    ):
        keys.append("cache_archive")
    downloaded: dict[str, Path] = {}
    for key in keys:
        remote = result.get(key)
        expected = f"output/{'workspace' if key == 'workspace_archive' else 'cache'}.tar.gz"
        if remote != expected:
            raise FleetPlanInvalid(f"OpenSandbox Fleet runner result has invalid {key}")
        destination = local / expected
        destination.parent.mkdir(parents=True, exist_ok=True)
        _require_cli_success(
            _run_osb(
                settings,
                "file",
                "download",
                sandbox_id,
                f"/fleet/{expected}",
                str(destination),
                "-o",
                "json",
                timeout=_command_timeout(timeout),
                cancel_requested=cancel_requested,
            ),
            f"download Fleet sandbox {key}",
        )
        downloaded[key] = destination
    return downloaded
