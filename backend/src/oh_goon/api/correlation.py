from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = b"x-request-id"


class RequestCorrelationMiddleware:
    """Assign one server-owned correlation ID to every HTTP request and response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = f"request-{uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, correlated_send)


def request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value is not None else None
