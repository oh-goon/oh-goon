from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.engine.command import CommandResult, RuntimeAuthorityReceipt
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations import _fleet_plan_opensandbox_transport as subject
from oh_goon.integrations.fleet_plan_runtime_authority import observed_runtime_authority_payload
from oh_goon.runtime.diagnostics import DiagnosticRouter


def test_remote_result_read_bounds_bytes_before_json_construction(tmp_path: Path) -> None:
    result_file = tmp_path / "result.json"
    payload = {"protocol": subject._RUNNER_PROTOCOL, "stdout": "ordinary result"}
    result_file.write_text(json.dumps(payload))
    assert subject._read_remote_result(result_file) == payload
    with result_file.open("r+b") as stream:
        stream.truncate(17 * 1024 * 1024)
    with pytest.raises(FleetPlanInvalid, match="exceeds its source-size bound"):
        subject._read_remote_result(result_file)
    result_file.write_bytes(b"\xff")
    with pytest.raises(FleetPlanInvalid, match="not valid JSON"):
        subject._read_remote_result(result_file)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        protocol="https",
        domain="sandbox.example",
        api_key="secret",
        image="runner@sha256:" + "a" * 64,
        runtime_attestation_sha256="b" * 64,
        cli_executable="/opt/osb",
    )


class _Response:
    def __init__(
        self, status_code: int = 200, payload: object = None, *, raise_error: bool = False
    ):
        self.status_code = status_code
        self._payload = payload
        self.raise_error = raise_error

    def raise_for_status(self) -> None:
        if self.raise_error:
            raise RuntimeError("http failure")

    def json(self) -> object:
        return self._payload


class _Http:
    def __init__(
        self,
        *,
        get: _Response | None = None,
        post: _Response | None = None,
        delete: _Response | None = None,
    ):
        self.get_response = get or _Response()
        self.post_response = post or _Response(202, {"id": "sandbox-1"})
        self.delete_response = delete or _Response(204)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("get", {"url": url, **kwargs}))
        return self.get_response

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("post", {"url": url, **kwargs}))
        return self.post_response

    def delete(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("delete", {"url": url, **kwargs}))
        return self.delete_response


def test_resource_api_and_environment_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert subject._opensandbox_resource(CicdResourcePolicy(cpu_millis=2000, memory_mib=64)) == {
        "cpu": "2",
        "memory": "64Mi",
    }
    assert (
        subject._opensandbox_resource(CicdResourcePolicy(cpu_millis=2500, memory_mib=64))["cpu"]
        == "2500m"
    )
    settings = _settings()
    assert subject._api_base(settings) == "https://sandbox.example/v1"
    assert subject._api_headers(settings)["OPEN-SANDBOX-API-KEY"] == "secret"
    monkeypatch.setenv("KEEP", "yes")
    env = subject._osb_environment(settings)
    assert env["KEEP"] == "yes"
    assert env["OPEN_SANDBOX_API_KEY"] == "secret"
    assert env["OPEN_SANDBOX_DOMAIN"] == "sandbox.example"
    assert subject._command_timeout(None) == 10.0
    assert subject._command_timeout(4.0) == 10.0
    assert subject._command_timeout(15.0) == 15.0


def test_probe_server_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    http = _Http()
    monkeypatch.setattr(subject, "_httpx2", lambda: http)
    subject._probe_server(settings)
    assert http.calls[0][0] == "get"
    http.get_response = _Response(500, raise_error=True)
    with pytest.raises(FleetPlanInvalid, match="not reachable"):
        subject._probe_server(settings)


def test_create_sandbox_binds_deny_all_resources_timeout_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    http = _Http(post=_Response(202, {"id": "  sb-1  "}))
    monkeypatch.setattr(subject, "_httpx2", lambda: http)
    sandbox = subject._create_sandbox(
        settings,
        resources=CicdResourcePolicy(cpu_millis=1500, memory_mib=128, max_processes=None),
        timeout=5.2,
    )
    assert sandbox == "sb-1"
    payload = http.calls[0][1]["json"]
    assert payload["networkPolicy"] == {"defaultAction": "deny", "egress": []}
    assert payload["resourceLimits"] == {"cpu": "1500m", "memory": "128Mi"}
    assert payload["timeout"] == 60
    assert payload["metadata"]["runtime-attestation"] == "b" * 64


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response(200, {"id": "x"}), "expected 202"),
        (_Response(202, {}), "missing sandbox id"),
        (_Response(202, {"id": 7}), "missing sandbox id"),
        (_Response(202, {"id": "  "}), "missing sandbox id"),
    ],
)
def test_create_sandbox_rejects_bad_provider_responses(
    monkeypatch: pytest.MonkeyPatch, response: _Response, message: str
) -> None:
    monkeypatch.setattr(subject, "_httpx2", lambda: _Http(post=response))
    with pytest.raises(FleetPlanInvalid, match=message):
        subject._create_sandbox(
            _settings(),
            resources=CicdResourcePolicy(max_processes=None),
            timeout=None,
        )


def test_create_sandbox_wraps_transport_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def post(self, *args: object, **kwargs: object) -> object:
            raise OSError("offline")

    monkeypatch.setattr(subject, "_httpx2", lambda: Broken())
    with pytest.raises(FleetPlanInvalid, match="creation failed"):
        subject._create_sandbox(
            _settings(), resources=CicdResourcePolicy(max_processes=None), timeout=None
        )


def test_destroy_sandbox_uses_api_and_falls_back_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    calls: list[tuple[str, ...]] = []

    def kill(_settings: object, *args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subject, "_run_osb", kill)
    monkeypatch.setattr(subject, "_httpx2", lambda: _Http(delete=_Response(204)))
    subject._destroy_sandbox(settings, "a")
    assert calls == []
    monkeypatch.setattr(subject, "_httpx2", lambda: _Http(delete=_Response(500)))
    subject._destroy_sandbox(settings, "b")
    assert calls[-1][:3] == ("sandbox", "kill", "b")


def test_destroy_sandbox_reports_api_and_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def delete(self, *args: object, **kwargs: object) -> object:
            raise OSError("offline")

    monkeypatch.setattr(subject, "_httpx2", lambda: Broken())
    monkeypatch.setattr(
        subject, "_run_osb", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cli"))
    )
    with pytest.raises(FleetPlanInvalid, match="cleanup could not be established"):
        subject._destroy_sandbox(_settings(), "sandbox")


def test_destroy_sandbox_rejects_nonzero_provider_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_httpx2", lambda: _Http(delete=_Response(500)))
    monkeypatch.setattr(
        subject,
        "_run_osb",
        lambda *args, **kwargs: CommandResult(returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(FleetPlanInvalid, match="provider kill exit=1"):
        subject._destroy_sandbox(_settings(), "sandbox")


@pytest.mark.parametrize("cancelled", (False, True))
def test_provider_cleanup_failure_preserves_result_and_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    events: list[DiagnosticEvent] = []
    cleanup: list[str] = []

    class RetainedSink:
        def write(self, event: DiagnosticEvent) -> None:
            events.append(event)

    class FailingSink:
        def write(self, event: DiagnosticEvent) -> None:
            raise OSError("diagnostic storage unavailable")

    request = SimpleNamespace(
        attempt=SimpleNamespace(
            paths=SimpleNamespace(root=tmp_path), resources=CicdResourcePolicy()
        ),
        diagnostics=DiagnosticRouter((FailingSink(), RetainedSink())),
        fleet_run_id="run-cleanup",
        step_id="build",
    )
    original = CommandResult(returncode=7, stdout="product output", stderr="product failure")
    cancellation = OperationCancelledError("original cancellation")
    monkeypatch.setattr(subject, "_create_sandbox", lambda *a, **kw: "sandbox-owned")

    def execute(*args: object, **kwargs: object) -> CommandResult:
        if cancelled:
            raise cancellation
        return original

    def evidence(*args: object) -> None:
        cleanup.append("evidence")
        raise OSError("provider-secret-must-not-be-logged")

    def destroy(*args: object) -> None:
        cleanup.append("destroy")
        raise OSError("provider-secret-must-not-be-logged")

    monkeypatch.setattr(subject, "_execute_open_sandbox_attempt", execute)
    monkeypatch.setattr(subject, "write_remote_resource_evidence", evidence)
    monkeypatch.setattr(subject, "_destroy_sandbox", destroy)
    options = dict(expected_runtime_authority=None, timeout=5, cancel_requested=None)
    if cancelled:
        with pytest.raises(OperationCancelledError) as captured:
            subject.OpenSandboxCliTransport().execute(_settings(), request, **options)
        assert captured.value is cancellation
        assert len(cancellation.__notes__) == 2
    else:
        result = subject.OpenSandboxCliTransport().execute(_settings(), request, **options)
        assert result.returncode == original.returncode
        assert result.stdout == original.stdout and result.supervisor is original.supervisor
        assert result.stderr.startswith("product failure\n")
        assert "provider-secret" not in result.stderr
    assert cleanup == ["evidence", "destroy"]
    assert len(events) == 2
    assert {event.failure_class for event in events} == {"provider_cleanup_incomplete"}
    assert all(event.fleet_run_id == "run-cleanup" and event.step_id == "build" for event in events)
    assert all("provider-secret" not in event.message for event in events)


def test_cli_success_and_returned_output_contracts() -> None:
    good = CommandResult(returncode=0, stdout="", stderr="", argv=("osb",), duration_ms=1)
    subject._require_cli_success(good, "probe")
    bad = CommandResult(returncode=4, stdout="", stderr="x" * 600, argv=("osb",), duration_ms=1)
    with pytest.raises(FleetPlanInvalid, match="exit=4"):
        subject._require_cli_success(bad, "probe")
    events: list[tuple[str, str]] = []
    subject._observe_returned_output(None, "out", "err")
    subject._observe_returned_output(lambda stream, text: events.append((stream, text)), "", "")
    subject._observe_returned_output(
        lambda stream, text: events.append((stream, text)), "out", "err"
    )
    assert events == [("stdout", "out"), ("stderr", "err")]


def test_remote_result_requires_current_helper_protocol(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text('{"protocol":' + str(subject._RUNNER_PROTOCOL) + ',"returncode":0}')
    assert subject._read_remote_result(result)["returncode"] == 0
    for protocol in ("3", "4", "true", '"5"', "null"):
        result.write_text('{"protocol":' + protocol + ',"returncode":0}')
        with pytest.raises(FleetPlanInvalid, match="unsupported protocol"):
            subject._read_remote_result(result)


def test_replace_tree_archive_rejects_invalid_tar_and_restores_tree(tmp_path: Path) -> None:
    destination = tmp_path / "dest"
    destination.mkdir()
    invalid = tmp_path / "invalid.tar.gz"
    invalid.write_bytes(b"not-a-tar")
    with pytest.raises(FleetPlanInvalid, match="archive is invalid"):
        subject._replace_tree_from_archive(destination, invalid)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("hello", encoding="utf-8")
    archive = tmp_path / "tree.tar.gz"
    subject._write_tree_archive(source, archive)
    subject._replace_tree_from_archive(destination, archive)
    assert (destination / "a.txt").read_text(encoding="utf-8") == "hello"
    assert archive.stat().st_size > 0


def _remote_runtime_archives(
    tmp_path: Path, *, archive_kind: str, members: tuple[tuple[str, bytes | str], ...]
) -> tuple[SimpleNamespace, dict[str, object], dict[str, Path]]:
    workspace, cache, runtime = (tmp_path / name for name in ("workspace", "cache", "runtime"))
    for root in (workspace, cache, runtime):
        root.mkdir()
    for root in (workspace, cache):
        (root / "prior.txt").write_bytes(b"existing attempt state")
    python = runtime / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"exact frozen Python bytes, not the host interpreter")
    python.chmod(0o755)
    archives: dict[str, Path] = {}
    for kind in ("workspace", "cache"):
        archive_path = tmp_path / f"{kind}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            selected = members if kind == archive_kind else (("unchanged.txt", b"untouched"),)
            for name, value in selected:
                member = tarfile.TarInfo(name)
                member.mode = 0o755 if isinstance(value, str) else 0o644
                if isinstance(value, str):
                    member.type = tarfile.SYMTYPE
                    member.linkname = value
                    archive.addfile(member)
                else:
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
        archives[f"{kind}_archive"] = archive_path
    request = SimpleNamespace(
        attempt=SimpleNamespace(
            paths=SimpleNamespace(workspace=workspace, cache=cache),
            runtime_root=SimpleNamespace(root=runtime),
            host_tools=(),
            sandbox_bindings=(),
        )
    )
    result: dict[str, object] = {
        "protocol": subject._RUNNER_PROTOCOL,
        "workspace_archive": "output/workspace.tar.gz",
        "cache_archive": "output/cache.tar.gz",
    }
    return request, result, archives


@pytest.mark.parametrize(
    ("archive_kind", "root"),
    (
        ("workspace", ".venv"),
        ("cache", "uv-project-environment"),
    ),
)
@pytest.mark.parametrize("link_target", ("/usr/bin/python3", "../../../usr/bin/python3"))
def test_remote_generated_interpreter_links_materialize_exact_frozen_authority(
    tmp_path: Path, archive_kind: str, root: str, link_target: str
) -> None:
    request, result, archives = _remote_runtime_archives(
        tmp_path,
        archive_kind=archive_kind,
        members=(
            (f"{root}/bin/python", link_target),
            (f"{root}/bin/python3", "python"),
            ("output.txt", b"application output"),
        ),
    )
    destination = getattr(request.attempt.paths, archive_kind)
    with pytest.raises(FleetPlanInvalid, match="archive is invalid"):
        subject._replace_tree_from_archive(destination, archives[f"{archive_kind}_archive"])
    assert (destination / "prior.txt").read_bytes() == b"existing attempt state"
    subject._restore_result_archives(request, result, archives)
    interpreter = destination / root / "bin/python"
    assert not interpreter.is_symlink()
    assert interpreter.read_bytes() == b"exact frozen Python bytes, not the host interpreter"
    assert interpreter.stat().st_mode & 0o777 == 0o755
    assert (destination / root / "bin/python3").read_bytes() == interpreter.read_bytes()
    assert (destination / "output.txt").read_bytes() == b"application output"


@pytest.mark.parametrize(
    ("archive_kind", "name", "target"),
    (
        ("workspace", "output/python", "/usr/bin/python3"),
        ("cache", "other-environment/bin/python", "/usr/bin/python3"),
        ("cache", "uv-project-environment", "/usr/bin/python3"),
        ("cache", "uv-project-environment/bin/python", "/private/host-secret"),
        ("cache", "uv-project-environment/bin/python", "/workspace/mutable"),
    ),
)
def test_remote_archive_external_links_fail_outside_original_runtime_authority(
    tmp_path: Path, archive_kind: str, name: str, target: str
) -> None:
    request, result, archives = _remote_runtime_archives(
        tmp_path,
        archive_kind=archive_kind,
        members=((name, target),),
    )
    with pytest.raises(FleetPlanInvalid, match="archive is invalid"):
        subject._restore_result_archives(request, result, archives)
    destination = getattr(request.attempt.paths, archive_kind)
    assert (destination / "prior.txt").read_bytes() == b"existing attempt state"
    assert not tuple(destination.parent.glob(".fleet-restore-*"))


def test_remote_archive_cannot_redirect_generated_link_through_prior_parent_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "python"
    protected.write_bytes(b"host file must remain unchanged")
    request, result, archives = _remote_runtime_archives(
        tmp_path,
        archive_kind="cache",
        members=(
            ("uv-project-environment/bin", str(outside)),
            ("uv-project-environment/bin/python", "/usr/bin/python3"),
        ),
    )
    with pytest.raises(FleetPlanInvalid, match="archive is invalid"):
        subject._restore_result_archives(request, result, archives)
    assert protected.read_bytes() == b"host file must remain unchanged"
    assert (request.attempt.paths.cache / "prior.txt").is_file()


@pytest.mark.parametrize("cancel_at", [None, "creation", "launch"])
def test_cancel_before_remote_work_preserves_cancellation_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_at: str | None
) -> None:
    calls: list[str] = []
    request = SimpleNamespace(
        argv=("true",),
        attempt=SimpleNamespace(
            paths=SimpleNamespace(root=tmp_path),
            resources=CicdResourcePolicy(max_processes=None),
        ),
    )
    receipt = RuntimeAuthorityReceipt(
        executable_path="/toolchain/bin/true",
        executable_identity="sha256:" + "a" * 64,
        executable_byte_count=1,
        path_authority_identity="sha256:" + "b" * 64,
        environment_identity="sha256:" + "c" * 64,
    )
    successful = CommandResult(returncode=0, stdout="", stderr="", argv=("osb",))

    def create(*args: object, **kwargs: object) -> str:
        calls.append("create")
        return "sandbox-1"

    def run(_settings: object, *args: str, **kwargs: object) -> CommandResult:
        if args[:2] == ("file", "upload"):
            calls.append("upload-request")
        elif args[:2] == ("file", "download"):
            calls.append("download-result")
        elif "mkdir" in args:
            calls.append("mkdir")
        else:
            calls.append("workload")
        return successful

    monkeypatch.setattr(subject, "_create_sandbox", create)
    monkeypatch.setattr(subject, "_run_osb", run)
    monkeypatch.setattr(subject, "_request_payload", lambda *a, **kw: {})
    monkeypatch.setattr(subject, "_upload_staged_inputs", lambda *a, **kw: None)
    monkeypatch.setattr(subject, "_download_result_archives", lambda *a, **kw: {})
    monkeypatch.setattr(subject, "_restore_result_archives", lambda *a, **kw: None)
    monkeypatch.setattr(
        subject,
        "_read_remote_result",
        lambda *a: {
            "returncode": 0,
            "runtime_authority": observed_runtime_authority_payload(receipt),
        },
    )
    monkeypatch.setattr(
        subject, "write_remote_resource_evidence", lambda *a: calls.append("evidence")
    )
    monkeypatch.setattr(subject, "_destroy_sandbox", lambda *a: calls.append("destroy"))

    def cancelled() -> bool:
        return cancel_at == "creation" or (cancel_at == "launch" and "upload-request" in calls)

    transport = subject.OpenSandboxCliTransport()
    if cancel_at is None:
        result = transport.execute(
            _settings(),
            request,
            expected_runtime_authority=receipt,
            timeout=10,
            cancel_requested=cancelled,
        )
        assert result.returncode == 0 and result.runtime_authority == receipt
        assert calls == [
            "create",
            "mkdir",
            "upload-request",
            "workload",
            "download-result",
            "evidence",
            "destroy",
        ]
    else:
        with pytest.raises(OperationCancelledError):
            transport.execute(
                _settings(),
                request,
                expected_runtime_authority=receipt,
                timeout=10,
                cancel_requested=cancelled,
            )
        assert calls == (
            []
            if cancel_at == "creation"
            else ["create", "mkdir", "upload-request", "evidence", "destroy"]
        )
