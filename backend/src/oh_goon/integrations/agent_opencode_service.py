"""Explicit OpenCode V2 readiness, without native-store access or model admission."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import SecretStr

from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.agent_providers import AgentProviderError

if TYPE_CHECKING:
    import httpx2

_MAX_READINESS_BYTES = 64 * 1024
_SUPPORTED_VERSION = "2.1.2"


class OpenCodeServiceError(AgentProviderError):
    """A safely bounded connection failure, never raw native service output."""


@dataclass(frozen=True, slots=True)
class OpenCodeServiceMetadata:
    """Observed native facts; neither account readiness nor execution authority."""

    version: str
    pid: int


def validate_opencode_service_endpoint(endpoint: str) -> str:
    """Normalize one explicit numeric loopback HTTP authority, without I/O."""
    message = "OpenCode service requires an explicit numeric loopback HTTP URL and port"
    try:
        if (
            not isinstance(endpoint, str)
            or not endpoint
            or any(character.isspace() or ord(character) < 32 for character in endpoint)
            or any(character in endpoint for character in ("@", "?", "#", "%", "\\"))
        ):
            raise ValueError("invalid endpoint")
        selected = urlsplit(endpoint)
        if selected.scheme != "http" or selected.path or not selected.hostname:
            raise ValueError("invalid HTTP authority")
        address = ipaddress.ip_address(selected.hostname)
        port = selected.port
        if not address.is_loopback or port is None or not 1 <= port <= 65535:
            raise ValueError("invalid loopback address or port")
    except (TypeError, ValueError):
        raise OpenCodeServiceError(message) from None
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{host}:{port}"


class OpenCodeServiceClient:
    """Connect only to the selected service; never discover, start or stop it.

    The broad transport password stays at this control boundary. No generic
    request or session execution API is exposed while native tool/private-store
    separation is unqualified.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        password: SecretStr,
        timeout_seconds: float = 10,
    ) -> None:
        self._endpoint = validate_opencode_service_endpoint(endpoint)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise OpenCodeServiceError("OpenCode service timeout must be finite and positive")
        if not isinstance(password, SecretStr):
            raise OpenCodeServiceError("OpenCode service requires an explicit private password")
        try:
            password_bytes = password.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise OpenCodeServiceError(
                "OpenCode service requires a valid private password"
            ) from None
        if not password_bytes or len(password_bytes) > 4096:
            raise OpenCodeServiceError(
                "OpenCode service requires a bounded nonempty private password"
            )
        self._password = password
        self._timeout = float(timeout_seconds)
        self._client: httpx2.AsyncClient | None = None
        self._response: httpx2.Response | None = None
        self._metadata: OpenCodeServiceMetadata | None = None
        self._closed = False

    @property
    def metadata(self) -> OpenCodeServiceMetadata | None:
        return self._metadata

    async def connect(self) -> OpenCodeServiceMetadata:
        """Bound the complete readiness exchange, including a trickled body."""
        if self._closed:
            raise OpenCodeServiceError("OpenCode service client is closed")
        if self._metadata is not None:
            return self._metadata
        if self._client is not None:
            raise OpenCodeServiceError("OpenCode service connection is already in progress")
        import httpx2

        try:
            async with asyncio.timeout(self._timeout):
                self._client = httpx2.AsyncClient(
                    timeout=self._timeout,
                    auth=("opencode", self._password.get_secret_value()),
                    follow_redirects=False,
                    trust_env=False,
                )
                request = self._client.build_request(
                    "GET",
                    self._endpoint + "/api/info",
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                )
                response = self._response = await self._client.send(request, stream=True)
                if response.status_code != 200:
                    raise OpenCodeServiceError("Selected OpenCode service rejected readiness")
                if response.headers.get("Content-Encoding", "identity").strip().lower() not in {
                    "",
                    "identity",
                }:
                    raise OpenCodeServiceError(
                        "OpenCode readiness requires identity content encoding"
                    )
                declared = response.headers.get("Content-Length", "")
                if declared.isdecimal() and int(declared) > _MAX_READINESS_BYTES:
                    raise OpenCodeServiceError("OpenCode readiness exceeds its byte limit")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if len(data) + len(chunk) > _MAX_READINESS_BYTES:
                        raise OpenCodeServiceError("OpenCode readiness exceeds its byte limit")
                    data.extend(chunk)
                metadata = _readiness_metadata(data)
                await response.aclose()
                self._response = None
            self._metadata = metadata
        except BaseException as primary:
            reported: BaseException = primary
            if isinstance(primary, Exception) and not isinstance(primary, OpenCodeServiceError):
                reported = OpenCodeServiceError(
                    "Cannot establish selected OpenCode service readiness"
                )
            try:
                await self.close()
            except BaseException:
                reported.add_note("OpenCode service transport cleanup was not verified")
            if reported is primary:
                raise
            raise reported from None
        return self._metadata

    def require_protected_execution(self) -> None:
        """No current public native policy proves private-store/tool separation."""
        raise CapabilityUnavailable(
            "Protected OpenCode service execution is unavailable: native tool isolation "
            "and authentication/configuration-store exclusion are unqualified"
        )

    async def close(self) -> None:
        """Bound client cleanup without stopping the service or hiding interruption."""
        self._closed = True
        response, self._response = self._response, None
        client, self._client = self._client, None
        deadline = asyncio.get_running_loop().time() + self._timeout
        primary: BaseException | None = None
        for resource in (response, client):
            if resource is None:
                continue
            try:
                async with asyncio.timeout_at(deadline):
                    await resource.aclose()
            except BaseException as error:
                if primary is None:
                    primary = error
        if primary is not None:
            if not isinstance(primary, Exception):
                raise primary
            raise OpenCodeServiceError("OpenCode service transport cleanup failed") from None

    async def __aenter__(self) -> OpenCodeServiceClient:
        await self.connect()
        return self

    async def __aexit__(
        self, _kind: object, primary: BaseException | None, _traceback: object
    ) -> None:
        try:
            await self.close()
        except BaseException:
            if primary is None:
                raise
            primary.add_note("OpenCode service transport cleanup was not verified")


def _readiness_metadata(data: bytes | bytearray) -> OpenCodeServiceMetadata:
    try:
        payload = json.loads(data, object_pairs_hook=_unique_fields, parse_constant=_nonfinite)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        version, pid = payload.get("version"), payload.get("pid")
        if version != _SUPPORTED_VERSION or type(pid) is not int or pid <= 0:
            raise ValueError("unsupported native identity")
    except (ValueError, UnicodeError, RecursionError):
        raise OpenCodeServiceError("OpenCode service returned incompatible V2 readiness") from None
    return OpenCodeServiceMetadata(version=version, pid=pid)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON field")
        result[name] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ValueError("nonfinite JSON value")
