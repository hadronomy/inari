from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from scalar_fastapi import AgentScalarConfig, Layout, Theme, get_scalar_api_reference

from ..application.container import (
    AgentContainer,
    build_container,
    get_default_container,
)
from ..config import AgentSettings
from ..core.logging import configure_logging
from ..core.version import API_VERSION, SERVICE_NAME
from ..client_trust import Permission
from .header_authorization import (
    AuthorizationMode,
    EndpointAuthorizationPolicy,
    ExplicitEndpointPolicyCatalog,
    HeaderAuthorizationMiddleware,
    ProblemAuthorizationErrorMapper,
)
from .middleware import install_security_middleware
from .problem_handlers import install_problem_handlers
from .routes import router


def operation_id(route: APIRoute) -> str:
    """Return the stable, human-authored function name for client generation."""

    return route.name


@asynccontextmanager
async def lifespan(app: FastAPI):
    container: AgentContainer = app.state.container
    configure_logging(
        container.settings.log_level,
        log_dir=container.settings.log_dir or "./logs",
    )
    container.database_migrator.ensure_current()
    supervisor = container.application_supervisor or container.runtime_supervisor
    await supervisor.start()
    try:
        yield
    finally:
        await supervisor.stop()


def create_app(
    settings: AgentSettings | None = None, *, container: AgentContainer | None = None
) -> FastAPI:
    app_container = container or (
        build_container(settings) if settings is not None else get_default_container()
    )
    app_settings = app_container.settings
    if app_container.security_policy_service is not None:
        app_container.security_policy_service.validate_startup()
    app = FastAPI(
        title=SERVICE_NAME,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        generate_unique_id_function=operation_id,
    )
    app.state.container = app_container
    if app_container.device_work_authorizer is not None:
        app.add_middleware(
            HeaderAuthorizationMiddleware,
            catalog=ExplicitEndpointPolicyCatalog(
                {
                    ("POST", "/v1/device-work"): EndpointAuthorizationPolicy(
                        AuthorizationMode.CLIENT_GRANT,
                        permission=Permission.RECEIPT_IMAGE,
                        name="receipt image submission",
                    ),
                    ("POST", "/v1/jobs/query"): EndpointAuthorizationPolicy(
                        AuthorizationMode.CLIENT_GRANT,
                        permission=Permission.JOBS_READ,
                        name="Print Job reconciliation",
                    ),
                    ("GET", "/v1/jobs/{job_id}"): EndpointAuthorizationPolicy(
                        AuthorizationMode.CLIENT_GRANT,
                        permission=Permission.JOBS_READ,
                        name="Print Job lookup",
                    ),
                }
            ),
            authorizer=app_container.device_work_authorizer,
            error_mapper=ProblemAuthorizationErrorMapper(),
        )
    if app_container.security_policy_service is not None:
        install_security_middleware(
            app, policy_service=app_container.security_policy_service
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["DPoP-Nonce", "Date", "X-Correlation-ID"],
    )
    app.include_router(router)
    install_problem_handlers(app)

    @app.get("/docs", include_in_schema=False)
    async def scalar_docs():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=f"{SERVICE_NAME} API Reference",
            theme=Theme.DEFAULT,
            layout=Layout.MODERN,
            show_sidebar=True,
            hide_dark_mode_toggle=False,
            telemetry=False,
            agent=AgentScalarConfig(disabled=True),
        )

    return app


app = create_app()
