from __future__ import annotations

import os
import secrets
from collections.abc import Awaitable, Callable
from hmac import compare_digest

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from oh_goon.api.errors import ApiErrorResponse
from oh_goon.core.errors import ErrorKind
from oh_goon.core import local_access_protocol

LOCAL_ACCESS_HEADER = "X-Oh-Goon-Local-Access"
LOCAL_ACCESS_COOKIE = "oh_goon_local_access"
LOCAL_ACCESS_EXCHANGE_PATH = "/api/local-session"

RequestHandler = Callable[[Request], Awaitable[Response]]


def new_local_access_token() -> str:
    """Return a process-local 256-bit capability for the local control plane."""

    return secrets.token_urlsafe(32)


def ensure_local_access_token() -> str:
    """Return the configured token, creating one only in this process environment."""

    configured = os.environ.get(local_access_protocol.LOCAL_ACCESS_ENV)
    if configured:
        return configured
    token = new_local_access_token()
    os.environ[local_access_protocol.LOCAL_ACCESS_ENV] = token
    return token


def configured_local_access_token() -> str:
    """Fail closed when the ASGI app was started without an explicit launch owner."""

    token = os.environ.get(local_access_protocol.LOCAL_ACCESS_ENV)
    if not token:
        token = new_local_access_token()
    return token


class LocalAccessMiddleware(BaseHTTPMiddleware):
    """Require one process-local capability for every domain API request."""

    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path == LOCAL_ACCESS_EXCHANGE_PATH:
            return await call_next(request)
        supplied = request.cookies.get(LOCAL_ACCESS_COOKIE) or request.headers.get(
            LOCAL_ACCESS_HEADER
        )
        if supplied is not None and compare_digest(supplied, self._token):
            return await call_next(request)
        payload = ApiErrorResponse(
            code="LOCAL_ACCESS_REQUIRED",
            kind=ErrorKind.INVALID_REQUEST,
            message="Local access authorization is required",
        )
        return JSONResponse(status_code=401, content=payload.model_dump(mode="json"))


def install_local_access_exchange(app: FastAPI, *, token: str) -> None:
    """Install the transport-only browser bootstrap endpoint outside domain OpenAPI."""

    @app.get(LOCAL_ACCESS_EXCHANGE_PATH, include_in_schema=False)
    async def local_session_status(request: Request) -> Response:
        supplied = request.cookies.get(LOCAL_ACCESS_COOKIE) or request.headers.get(
            LOCAL_ACCESS_HEADER
        )
        if supplied is None or not compare_digest(supplied, token):
            return Response(status_code=401)
        return Response(status_code=204)

    @app.post(LOCAL_ACCESS_EXCHANGE_PATH, include_in_schema=False)
    async def establish_local_session(request: Request) -> Response:
        supplied = request.headers.get(LOCAL_ACCESS_HEADER)
        if supplied is None or not compare_digest(supplied, token):
            return Response(status_code=401)
        response = Response(status_code=204)
        response.set_cookie(
            LOCAL_ACCESS_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/api",
        )
        return response
