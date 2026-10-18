from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import _fleet_plan_opensandbox_transport as subject


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
    monkeypatch.setattr(subject, "_run_osb", lambda _settings, *args, **kwargs: calls.append(args))
    monkeypatch.setattr(subject, "_httpx2", lambda: _Http(delete=_Response(204)))
    subject._destroy_sandbox(settings, "a")
    assert calls == []
    monkeypatch.setattr(subject, "_httpx2", lambda: _Http(delete=_Response(500)))
    subject._destroy_sandbox(settings, "b")
    assert calls[-1][:3] == ("sandbox", "kill", "b")


def test_destroy_sandbox_swallows_api_and_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def delete(self, *args: object, **kwargs: object) -> object:
            raise OSError("offline")

    monkeypatch.setattr(subject, "_httpx2", lambda: Broken())
    monkeypatch.setattr(
        subject, "_run_osb", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cli"))
    )
    subject._destroy_sandbox(_settings(), "sandbox")


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
