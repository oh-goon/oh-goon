from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from oh_goon.api.correlation import request_id
from oh_goon.core.errors import ErrorKind, FleetDomainError
from oh_goon.core.models.diagnostic import DiagnosticEvent


class ApiErrorResponse(BaseModel):
    code: str
    kind: ErrorKind
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


def common_error_responses() -> dict[int | str, dict[str, object]]:
    return {
        400: {"model": ApiErrorResponse, "description": "Invalid request"},
        404: {"model": ApiErrorResponse, "description": "Resource not found"},
        409: {"model": ApiErrorResponse, "description": "Current state conflicts with the request"},
        422: {"model": ApiErrorResponse, "description": "Request validation failed"},
        500: {"model": ApiErrorResponse, "description": "Internal domain failure"},
    }


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        payload = ApiErrorResponse.model_validate({"kind": _status_kind(exc.status_code), **detail})
    else:
        payload = ApiErrorResponse(
            code=_status_code(exc.status_code),
            kind=_status_kind(exc.status_code),
            message=str(detail),
            retryable=exc.status_code in {408, 429, 503},
        )
    payload.request_id = request_id(request)
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # T5 inspection receives caller-supplied Plan input values, potentially secrets.
    # Pydantic error contexts can include rejected input data; never return them.
    if request.url.path == "/api/league/shared-plan-input-inspections":
        payload = ApiErrorResponse(
            code="VALIDATION_ERROR", kind=ErrorKind.INVALID_REQUEST,
            message="Request validation failed", request_id=request_id(request),
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))
    errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={Exception: str},
    )
    payload = ApiErrorResponse(
        code="VALIDATION_ERROR",
        kind=ErrorKind.INVALID_REQUEST,
        message="Request validation failed",
        details={"errors": errors},
        request_id=request_id(request),
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


async def domain_exception_handler(request: Request, exc: FleetDomainError) -> JSONResponse:
    status_code = _domain_status(exc)
    payload = ApiErrorResponse(
        code=exc.code,
        kind=exc.kind,
        message=str(exc),
        retryable=exc.retryable,
        request_id=request_id(request),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = request_id(request)
    diagnostics = getattr(request.app.state, "diagnostics", None)
    if diagnostics is not None:
        diagnostics.emit(
            DiagnosticEvent.from_exception(
                exc,
                source="http_request",
                owner="FastAPI",
                operation="handle_request",
                execution_affected=True,
                failure_class="unexpected_application",
                request_id=correlation_id,
                http_method=request.method,
                http_path=request.url.path,
            )
        )
    payload = ApiErrorResponse(
        code="INTERNAL_ERROR",
        kind=ErrorKind.INTERNAL,
        message="Unexpected internal error",
        retryable=False,
        request_id=correlation_id,
    )
    return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))


def _domain_status(exc: FleetDomainError) -> int:
    return {
        ErrorKind.INVALID_REQUEST: 400,
        ErrorKind.NOT_FOUND: 404,
        ErrorKind.CONFLICT: 409,
        ErrorKind.INTERNAL: 500,
    }[exc.kind]


def _status_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }.get(status_code, "REQUEST_FAILED")


def _status_kind(status_code: int) -> ErrorKind:
    if status_code in {400, 422}:
        return ErrorKind.INVALID_REQUEST
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 409:
        return ErrorKind.CONFLICT
    return ErrorKind.INTERNAL
