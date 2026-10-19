from __future__ import annotations

import ipaddress
import json
import socket
from pathlib import Path
from typing import Any


class LoopbackNetworkGuard:
    def __init__(self, audit_path: Path) -> None:
        self._audit_path = audit_path
        self._real_connect = socket.socket.connect
        self._real_connect_ex = socket.socket.connect_ex
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        guard = self

        def guarded_connect(sock: socket.socket, address: Any) -> None:
            if not _allowed_address(address):
                guard._record(address, "connect")
                raise OSError("Oh Goon certification blocked non-loopback outbound TCP")
            return guard._real_connect(sock, address)

        def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
            if not _allowed_address(address):
                guard._record(address, "connect_ex")
                return 101
            return guard._real_connect_ex(sock, address)

        socket.socket.connect = guarded_connect  # type: ignore[method-assign, assignment]
        socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign, assignment]
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        socket.socket.connect = self._real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._real_connect_ex  # type: ignore[method-assign]
        self._installed = False

    def __enter__(self) -> "LoopbackNetworkGuard":
        self.install()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.uninstall()

    def _record(self, address: object, operation: str) -> None:
        payload = {"operation": operation, "address": repr(address)}
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _allowed_address(address: object) -> bool:
    if isinstance(address, (str, bytes)):
        return True
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if not isinstance(host, str):
        return False
    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
