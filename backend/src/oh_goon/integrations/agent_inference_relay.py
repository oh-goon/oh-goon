"""In-boundary loopback-to-UDS transport, with no provider configuration or secrets.

The host broker validates every HTTP request, including direct socket clients.
The relay only moves bounded chunks; native children use the existing command owner.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import select
import socket
import socketserver
import sys
import threading
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.core.models.fleet_plan_step_primitives import PLAN_CONTRACT_RETURN_CODE
from oh_goon.core.models.pi_session_storage import PI_SESSION_OBSERVATION_RECORD
from oh_goon.engine.command import MAX_COMMAND_INPUT_BYTES, CommandTerminal, run_command
from oh_goon.integrations.agent_pi_rpc import PiRpcContinuation, PiRpcControl


@dataclass(frozen=True, slots=True)
class InferenceRelay:
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class _RelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, socket_path: Path, port: int) -> None:
        self.socket_path = socket_path
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.connections: set[socket.socket] = set()
        super().__init__(("127.0.0.1", port), _RelayHandler)

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        with self.lock:
            if self.stopped.is_set() or len(self.connections) >= 32:
                connection.close()
                raise OSError("inference relay unavailable")
            self.connections.add(connection)
        connection.settimeout(1)
        return connection, address

    def shutdown_request(self, request: socket.socket | tuple[bytes, socket.socket]) -> None:
        with self.lock:
            if isinstance(request, socket.socket):
                self.connections.discard(request)
        super().shutdown_request(request)

    def handle_error(self, request: Any, client_address: Any) -> None:
        return

    def close_connections(self) -> None:
        self.stopped.set()
        with self.lock:
            connections = tuple(self.connections)
        for connection in connections:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)


class _RelayHandler(socketserver.BaseRequestHandler):
    # _RelayServer constructs this handler with itself as the native server.
    server: _RelayServer  # ty: ignore[invalid-mutable-override]

    def handle(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.settimeout(1)
            with self.server.lock:
                self.server.connections.add(upstream)
            try:
                upstream.connect(str(self.server.socket_path))
                readers = [self.request, upstream]
                while readers and not self.server.stopped.is_set():
                    ready, _, _ = select.select(readers, [], [], 0.1)
                    for source in ready:
                        target = upstream if source is self.request else self.request
                        data = source.recv(65536)
                        if not data:
                            readers.remove(source)
                            with contextlib.suppress(OSError):
                                target.shutdown(socket.SHUT_WR)
                            if source is upstream:
                                return
                        else:
                            try:
                                target.sendall(data)
                            except (BrokenPipeError, ConnectionResetError):
                                if source is upstream:
                                    raise
                                # The broker can refuse headers before reading a body.
                                # Keep its response half open after request refusal.
                                readers.remove(source)
                                with contextlib.suppress(OSError):
                                    upstream.shutdown(socket.SHUT_WR)
            except (OSError, ValueError):
                return
            finally:
                with self.server.lock:
                    self.server.connections.discard(upstream)


@contextlib.contextmanager
def model_inference_relay(
    socket_path: Path, *, port: int = 18743
) -> Generator[InferenceRelay, None, None]:
    """Expose only a namespace-local listener; the host endpoint stays a fixed UDS."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("inference relay port must be between 0 and 65535")
    server = _RelayServer(socket_path, port)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, name="oh-goon-inference-relay"
    )
    started = False
    try:
        thread.start()
        started = True
        yield InferenceRelay(server.server_address[1])
    finally:
        server.close_connections()
        if started:
            server.shutdown()
        server.server_close()
        if started:
            thread.join()


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        count = os.write(descriptor, remaining)
        if count <= 0:
            raise OSError("terminal output closed")
        remaining = remaining[count:]


@contextlib.contextmanager
def _interactive_terminal() -> Generator[CommandTerminal, None, None]:
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    output = sys.stdout.fileno()
    if not os.isatty(descriptor) or not os.isatty(output):
        raise ValueError("interactive inference relay requires terminal input and output")

    def read_input(maximum: int) -> bytes | None:
        ready, _, _ = select.select([descriptor], [], [], 0)
        return os.read(descriptor, maximum) if ready else None

    def size() -> tuple[int, int]:
        current = os.get_terminal_size(descriptor)
        return current.lines, current.columns

    attributes = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        yield CommandTerminal(
            read_input,
            lambda data: _write_all(output, data),
            initial_size=size(),
            current_size=size,
        )
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, attributes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relay only the approved local inference channel")
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18743)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--pi-rpc", action="store_true")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--pi-session-new", action="store_true")
    session.add_argument("--pi-session-continuation", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    command = options.command[1:] if options.command[:1] == ["--"] else options.command
    if not command or (options.interactive and options.input_file is not None):
        parser.error("supply a native command and exactly one terminal/input mode")
    if options.pi_rpc and (options.interactive or options.input_file is None):
        parser.error("Pi RPC requires noninteractive frozen prompt input")
    if (options.pi_session_new or options.pi_session_continuation) and not options.pi_rpc:
        parser.error("Private Pi session observations require Pi RPC")
    data = b""
    if options.input_file is not None:
        with options.input_file.open("rb") as stream:
            data = stream.read(MAX_COMMAND_INPUT_BYTES + 1)
        if len(data) > MAX_COMMAND_INPUT_BYTES:
            parser.error("native input exceeds the command owner's byte bound")
    continuation = None
    if options.pi_session_continuation is not None:
        try:
            with options.pi_session_continuation.open("rb") as stream:
                payload = stream.read(4097)
            if len(payload) > 4096:
                raise ValueError("Oversize Pi continuation metadata")
            continuation = PiRpcContinuation.model_validate_json(payload)
        except (OSError, ValueError):
            print("Pi RPC continuation authority is unavailable", file=sys.stderr)
            return PLAN_CONTRACT_RETURN_CODE
    try:
        rpc = (
            PiRpcControl(data, persistent=options.pi_session_new, continuation=continuation)
            if options.pi_rpc
            else None
        )
    except AgentProviderError as error:
        print(str(error), file=sys.stderr)
        return PLAN_CONTRACT_RETURN_CODE

    def stream_output(stream: str, text: str) -> None:
        if rpc is not None and stream == "stdout":
            # Deliver each LF boundary separately so a later bad record cannot
            # erase native events already observed earlier in this chunk.
            parts = text.split("\n")
            for index, part in enumerate(parts):
                fragment = part + ("\n" if index < len(parts) - 1 else "")
                try:
                    records = rpc.feed_stdout(fragment)
                except AgentProviderError:
                    # The latched failure closes child input. It must also fail
                    # the relay after native disposal, even if the child exits 0.
                    return
                for record in records:
                    _write_all(1, record.encode("utf-8"))
        else:
            _write_all(1 if stream == "stdout" else 2, text.encode("utf-8"))

    with model_inference_relay(options.socket, port=options.port):
        terminal = _interactive_terminal() if options.interactive else contextlib.nullcontext(None)
        with terminal as transport:
            result = run_command(
                *command,
                timeout=None,
                terminal=transport,
                input_data=None if transport is not None or rpc is not None else data,
                input_source=rpc,
                parent_death_signal=True,
                preserve_native_executable_path=True,
                retain_output_tail=transport is None,
                require_process_group_quiescence=True,
                max_capture_bytes_per_stream=MAX_COMMAND_INPUT_BYTES,
                stream_output=(None if transport is not None else stream_output),
            )
    if rpc is not None and result.returncode == 0:
        try:
            observation = rpc.finish()
        except AgentProviderError as error:
            print(str(error), file=sys.stderr)
            return PLAN_CONTRACT_RETURN_CODE
        if observation is not None:
            if result.supervisor.final_process_group_quiescent is not True:
                return PLAN_CONTRACT_RETURN_CODE
            _write_all(
                1,
                json.dumps(
                    {"type": PI_SESSION_OBSERVATION_RECORD, "data": observation.model_dump()},
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n",
            )
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
