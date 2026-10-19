from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from oh_goon import __version__
from oh_goon.api.correlation import RequestCorrelationMiddleware
from oh_goon.api.dependencies import application
from oh_goon.api.errors import (
    common_error_responses,
    domain_exception_handler,
    http_exception_handler,
    unexpected_exception_handler,
    validation_exception_handler,
)
from oh_goon.api.local_access import (
    LocalAccessMiddleware,
    configured_local_access_token,
    install_local_access_exchange,
)
from oh_goon.api.routes import (
    capabilities,
    dependencies,
    filesystem,
    fleet_command_center,
    fleet_executors,
    fleet_migration,
    fleet_runs,
    fleet_security,
    fleet_snapshot,
    health,
    league,
    locations,
    privacy,
    recovery,
    repositories,
    schedules,
    scout,
)
from oh_goon.api.security import LocalOnlySecurityHeadersMiddleware
from oh_goon.api.ui import mount_built_ui
from oh_goon.core.errors import FleetDomainError
from oh_goon.runtime.local_launch_owner import record_backend_startup_complete_from_environment


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    runtime = application()
    _app.state.diagnostics = runtime.diagnostics
    runtime.start_background()
    record_backend_startup_complete_from_environment()
    try:
        yield
    finally:
        runtime.shutdown(wait_for_runs=False)


def create_app(
    *,
    local_access_token: str | None = None,
    product_version: str | None = None,
) -> FastAPI:
    access_token = local_access_token or configured_local_access_token()
    app = FastAPI(
        title="oh-goon API",
        version=product_version or __version__,
        lifespan=_lifespan,
    )
    app.state.local_access_token = access_token
    app.state.diagnostics = None

    @app.get("/healthz", include_in_schema=False)
    async def liveness() -> Response:
        return Response(status_code=204)

    install_local_access_exchange(app, token=access_token)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(FleetDomainError, domain_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)
    app.add_middleware(RequestCorrelationMiddleware)
    app.add_middleware(LocalAccessMiddleware, token=access_token)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
    )
    app.add_middleware(LocalOnlySecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Oh-Goon-Local-Access"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(health.router, prefix="/api", responses=common_error_responses())
    app.include_router(capabilities.router, prefix="/api", responses=common_error_responses())
    app.include_router(privacy.router, prefix="/api", responses=common_error_responses())
    app.include_router(filesystem.router, prefix="/api", responses=common_error_responses())
    app.include_router(locations.router, prefix="/api", responses=common_error_responses())
    app.include_router(league.router, prefix="/api", responses=common_error_responses())
    app.include_router(repositories.router, prefix="/api", responses=common_error_responses())
    app.include_router(fleet_snapshot.router, prefix="/api", responses=common_error_responses())
    app.include_router(dependencies.router, prefix="/api", responses=common_error_responses())
    app.include_router(
        fleet_command_center.router, prefix="/api", responses=common_error_responses()
    )
    app.include_router(fleet_security.router, prefix="/api", responses=common_error_responses())
    app.include_router(fleet_executors.router, prefix="/api", responses=common_error_responses())
    app.include_router(fleet_migration.router, prefix="/api", responses=common_error_responses())
    app.include_router(fleet_runs.router, prefix="/api", responses=common_error_responses())
    app.include_router(schedules.router, prefix="/api", responses=common_error_responses())
    app.include_router(scout.router, prefix="/api", responses=common_error_responses())
    app.include_router(recovery.router, prefix="/api", responses=common_error_responses())
    mount_built_ui(app)
    return app


app = create_app()
