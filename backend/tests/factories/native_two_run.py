"""Selected real Pi/local-provider qualification through original product owners."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.execution.sqlite_lease import WorkerResourceReservation

PAIR_VERIFICATION_ARGV = (
    "/usr/bin/python3",
    "-I",
    "-c",
    "from pathlib import Path; assert Path('code.py').read_bytes() == b'VALUE = 42\\n'",
)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=repository, capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


def _texts(message: dict[str, Any]) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
    return []


@dataclass
class NativeTwoRunProvider:
    repositories: dict[str, Path]
    selected: dict[str, ResolvedNativeAgent]
    entered: dict[str, Event]
    release: dict[str, Event]
    written: dict[str, Event]
    finish: dict[str, Event]
    requests: dict[str, list[dict[str, Any]]]
    facts: dict[str, dict[str, Any]]
    errors: list[str]
    database_path: Path

    def release_all(self) -> None:
        for event in (*self.release.values(), *self.finish.values()):
            event.set()


@dataclass
class NativeTwoRunCase(NativeTwoRunProvider):
    runtime: ApplicationRuntime

    def start(self, label: str) -> FleetRunState:
        return self.runtime.plans.run_native_agent(
            selected=self.selected[label],
            workspace=self.repositories[label],
            skill=None,
            task=f"HUG-223 owner {label}: edit only this candidate and pass its independent verifier",
            candidate=True,
            fleet_run_id=f"two-run-{label}",
            verification=(PAIR_VERIFICATION_ARGV,),
        )

    def active_reservations(self) -> dict[str, WorkerResourceReservation]:
        database = LocalDatabase(self.database_path, read_only=True)
        owner = ExecutionCoordinator(database)
        with database.transaction() as connection:
            result = {}
            for label in ("a", "b"):
                value = owner.worker_resource_reservation_in(connection, f"two-run-{label}")
                assert value is not None
                result[label] = value
            return result


@contextmanager
def native_two_run_provider(
    root: Path,
    policy: RunConcurrencyPolicy,
    *,
    program: Path,
    public_runtime: Path,
    probe_namespaces: bool = False,
) -> Generator[NativeTwoRunProvider, None, None]:
    """No alternate executor: the fixture supplies only a selected model protocol."""
    root.mkdir(parents=True, exist_ok=True)
    version_home = root / "native-version-home"
    version_home.mkdir()
    version = subprocess.run(
        (str(program), "--version"),
        env={"PATH": os.defpath, "HOME": str(version_home)},
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert version.stdout.strip() == "1.3.0", "Requalify a different selected Pi implementation"
    implementation = native_executable_sha256(program)
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        os.environ[f"XDG_{kind}_HOME"] = str(root / kind.lower())
    os.environ["OH_GOON_DB"] = str(root / "state" / "application.sqlite")
    os.environ.pop("OH_GOON_EXECUTORS_FILE", None)
    repositories: dict[str, Path] = {}
    for label in ("a", "b", "third"):
        repository = root / "repositories" / label
        repository.mkdir(parents=True)
        (repository / "code.py").write_text(f"# Original repository {label}\nVALUE = 41\n")
        (repository / "verify.py").write_text("from code import VALUE\nassert VALUE == 42\n")
        _git(repository, "init", "-q")
        _git(repository, "add", ".")
        _git(
            repository,
            "-c",
            "user.name=Native Qualification",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "base",
        )
        repositories[label] = repository
    entered = {label: Event() for label in repositories}
    release = {label: Event() for label in repositories}
    written = {label: Event() for label in repositories}
    finish = {label: Event() for label in repositories}
    requests: dict[str, list[dict[str, Any]]] = {label: [] for label in repositories}
    facts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    tool_sent: set[str] = set()
    lock = Lock()

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                size = int(self.headers["Content-Length"])
                assert 0 < size <= 8 * 1024 * 1024
                body = json.loads(self.rfile.read(size))
                label = body["model"].removeprefix("hug223-")
                assert label in repositories
                with lock:
                    requests[label].append(body)
                    assert len(requests[label]) <= 5
                functions = [
                    item["function"]
                    for item in body.get("tools", [])
                    if isinstance(item.get("function"), dict)
                ]
                if not functions:
                    self.reply(
                        {"role": "assistant", "content": "Native candidate context loaded"}, "stop"
                    )
                    return
                if label not in tool_sent:
                    entered[label].set()
                    assert release[label].wait(90), "native source gate was not released"
                    tool = next(
                        item
                        for item in functions
                        if item.get("name") in {"bash", "shell", "shell_command", "exec_command"}
                    )
                    properties = tool["parameters"]["properties"]
                    source = f"import json,os,pathlib,tempfile\nowner={label!r}\n"
                    facts_source = "{'owner':owner,'cwd':str(pathlib.Path.cwd()),'home':str(pathlib.Path.home()),'tmp':tempfile.gettempdir(),'pid':os.getpid()}"
                    if probe_namespaces:
                        source += (
                            "namespace_probe={}\n"
                            "for kind,root in [('home',pathlib.Path.home()),('tmp',pathlib.Path(tempfile.gettempdir()))]:\n"
                            " marker=root/'.hug223-namespace-owner'\n"
                            " descriptor=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)\n"
                            " try: os.write(descriptor,owner.encode())\n"
                            " finally: os.close(descriptor)\n"
                            " assert marker.read_text()==owner\n"
                            " observed=root.stat()\n"
                            " namespace_probe[kind]=[observed.st_dev,observed.st_ino]\n"
                            "namespace_probe['mount_namespace']=os.readlink('/proc/self/ns/mnt')\n"
                        )
                        facts_source = facts_source[:-1] + ",'namespaces':namespace_probe}"
                    source += (
                        "pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
                        f"print('HUG223_FACTS:'+json.dumps({facts_source}))\n"
                    )
                    command = shlex.join(("/usr/bin/python3", "-c", source))
                    arguments = {"command" if "command" in properties else "cmd": command}
                    if "description" in properties:
                        arguments["description"] = "Edit only the admitted candidate"
                    assert set(tool["parameters"].get("required", [])).issubset(arguments)
                    tool_sent.add(label)
                    self.reply(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_candidate_" + label,
                                    "type": "function",
                                    "function": {
                                        "name": tool["name"],
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                        "tool_calls",
                    )
                    return
                for message in body["messages"]:
                    if message.get("role") == "tool":
                        for text in _texts(message):
                            marker = text.find("HUG223_FACTS:")
                            if marker >= 0:
                                value, _ = json.JSONDecoder().raw_decode(
                                    text[marker + len("HUG223_FACTS:") :]
                                )
                                assert value["owner"] == label
                                facts[label] = value
                assert label in facts, "the native shell did not produce its actual facts"
                written[label].set()
                assert finish[label].wait(90), "native finish gate was not released"
                self.reply(
                    {
                        "role": "assistant",
                        "content": "Candidate edited; run the independent verifier",
                    },
                    "stop",
                )
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
                self.send_error(500)

        def reply(self, delta: dict[str, Any], reason: str) -> None:
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
            ]
            data = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint)
    server.daemon_threads = True
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
        config = AgentProvidersConfig.model_validate(
            {
                "providers": {
                    "fixture": {
                        "api": "openai-chat-completions",
                        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    }
                },
                "models": {
                    label: {"provider": "fixture", "model": "hug223-" + label}
                    for label in repositories
                },
            }
        )
        selected = {
            label: ResolvedNativeAgent(
                agent="pi",
                profile_name="capacity-" + label,
                definition=NativeAgentDefinition(
                    executable=str(program), runtime_roots=(str(public_runtime),)
                ),
                profile=NativeAgentProfile(
                    backend="bubblewrap",
                    allowed_agents=("pi",),
                    inference_profile=label,
                    timeout_seconds=120,
                ),
                executable_sha256=implementation,
                connection=AgentConnection.select(config, label),
                backend_configuration_id=backend.configuration_identity(),
            )
            for label in repositories
        }
        yield NativeTwoRunProvider(
            repositories=repositories,
            selected=selected,
            entered=entered,
            release=release,
            written=written,
            finish=finish,
            requests=requests,
            facts=facts,
            errors=errors,
            database_path=root / "state" / "application.sqlite",
        )
    finally:
        for event in (*release.values(), *finish.values()):
            event.set()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@contextmanager
def native_two_run_case(
    root: Path,
    policy: RunConcurrencyPolicy,
    *,
    program: Path,
    public_runtime: Path,
    probe_namespaces: bool = False,
) -> Generator[NativeTwoRunCase, None, None]:
    """Layer the original application over the separately owned model endpoint."""
    with native_two_run_provider(
        root,
        policy,
        program=program,
        public_runtime=public_runtime,
        probe_namespaces=probe_namespaces,
    ) as provider:
        backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
        runtime = ApplicationRuntime.build(
            standard_local=True, standard_process_isolation=backend, run_concurrency=policy
        )
        runtime.locations.add(root / "repositories")
        case = NativeTwoRunCase(
            repositories=provider.repositories,
            selected=provider.selected,
            entered=provider.entered,
            release=provider.release,
            written=provider.written,
            finish=provider.finish,
            requests=provider.requests,
            facts=provider.facts,
            errors=provider.errors,
            database_path=provider.database_path,
            runtime=runtime,
        )
        try:
            yield case
        finally:
            case.release_all()
            runtime.shutdown(
                wait_for_runs=False, cancel_grace_seconds=2, containment_timeout_seconds=10
            )
