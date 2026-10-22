"""Explicit live qualification of native-managed Codex account refresh.

This test support never authors credentials. Codex alone signs in and refreshes;
it inspects the non-secret refresh timestamp privately and returns only boolean facts.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_MAX_RECORD = 1024 * 1024
_MAX_STREAM = 4 * _MAX_RECORD


class _NativeAccountRPC:
    def __init__(self, program: Path, home: Path) -> None:
        self.selector = selectors.DefaultSelector()
        try:
            self.process = subprocess.Popen(
                [
                    str(program),
                    "--no-daemon",
                    "app-server",
                    "--stdio",
                    "-c",
                    'model_provider="openai"',
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=home,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(home),
                    "CODEX_HOME": str(home),
                },
                bufsize=0,
            )
        except BaseException:
            self.selector.close()
            raise
        assert self.process.stdin is not None and self.process.stdout is not None
        try:
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except BaseException:
            self.close()
            raise
        self.pending = b""
        self.received = 0
        self.sequence = 0

    def __enter__(self) -> _NativeAccountRPC:
        try:
            self.request(
                "initialize",
                {"clientInfo": {"name": "oh_goon_live_qualification", "version": "0.1.0"}},
            )
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.close()
            raise

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        self.send({"method": method, "id": self.sequence, "params": params})
        deadline = time.monotonic() + 60
        for _ in range(128):
            while b"\n" not in self.pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.selector.select(remaining):
                    raise TimeoutError("Native account RPC deadline exceeded")
                assert self.process.stdout is not None
                chunk = os.read(
                    self.process.stdout.fileno(), min(65536, _MAX_RECORD + 1 - len(self.pending))
                )
                if not chunk:
                    raise RuntimeError("Native account RPC closed before a response")
                self.received += len(chunk)
                self.pending += chunk
                if len(self.pending) > _MAX_RECORD or self.received > _MAX_STREAM:
                    raise RuntimeError("Native account RPC exceeded its byte budget")
            line, self.pending = self.pending.split(b"\n", 1)
            message = json.loads(line)
            if message.get("id") != self.sequence:
                continue
            if "error" in message:
                raise RuntimeError("Native account RPC rejected " + method)
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Native account RPC returned an invalid result")
            return result
        raise RuntimeError("Native account RPC exceeded its message budget")

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        finally:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
            finally:
                try:
                    if self.process.stdout is not None:
                        self.process.stdout.close()
                finally:
                    self.selector.close()

    def __exit__(self, *_args: object) -> None:
        self.close()


def _refresh_timestamp(home: Path) -> datetime | None:
    # Explicit qualification inspects only the native non-secret timestamp.
    # Credential fields remain local and are never returned, hashed or logged.
    with (home / "auth.json").open("rb") as cache:
        data = cache.read(65537)
    if len(data) > 65536:
        raise RuntimeError("Native credential cache exceeds the qualification read bound")
    try:
        timestamp = json.loads(data).get("last_refresh")
        return datetime.fromisoformat(timestamp) if timestamp is not None else None
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError("Native refresh persistence metadata is invalid") from None


def refresh_native_chatgpt_account(program: Path, home: Path) -> dict[str, bool]:
    """Force native refresh, proving persistence without accepting a cached account."""
    with _NativeAccountRPC(program, home) as native:
        status = native.request("getAuthStatus", {"includeToken": False, "refreshToken": False})
        managed = status.get("authMethod") == "chatgpt" and status.get("requiresOpenaiAuth") is True
        token_omitted = status.get("authToken") is None
        if not managed or not token_omitted:
            raise RuntimeError("Live qualification requires native-managed ChatGPT authentication")
        before = native.request("account/read", {"refreshToken": False})
        previous_refresh = _refresh_timestamp(home)
        started = datetime.now(UTC)
        after = native.request("account/read", {"refreshToken": True})
        finished = datetime.now(UTC)
        current_refresh = _refresh_timestamp(home)
        before_account, after_account = before.get("account"), after.get("account")
        before_routing, after_routing = (
            before.get("workspaceRouting"),
            after.get("workspaceRouting"),
        )
        identity_preserved = bool(
            before_account
            and after_account
            and before_account.get("type") == after_account.get("type") == "chatgpt"
            and before_account.get("email") == after_account.get("email")
            and before_routing
            and after_routing
            and before_routing.get("chatgptAccountId")
            and before_routing.get("chatgptAccountId") == after_routing.get("chatgptAccountId")
        )
        return {
            "managed_chatgpt": managed,
            "auth_token_omitted": token_omitted,
            "refresh_advanced": current_refresh is not None
            and (previous_refresh is None or current_refresh > previous_refresh),
            "refresh_recorded_during_request": current_refresh is not None
            and started <= current_refresh <= finished,
            "account_identity_preserved": identity_preserved,
        }
