from __future__ import annotations

from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from scalar_fastapi import AgentScalarConfig, Layout, Theme, get_scalar_api_reference
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..application.container import (
    AgentContainer,
    build_container,
    get_default_container,
)
from ..config import AgentSettings
from ..core.exceptions import (
    AgentError,
    ErrorItemPayload,
    ErrorPayload,
    ErrorSourcePayload,
)
from ..core.logging import configure_logging
from ..core.version import API_VERSION, SERVICE_NAME
from .middleware import install_security_middleware
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
    # Progenitor currently consumes OpenAPI 3.0. FastAPI adapts nullable schemas
    # and other dialect-specific details when it builds this document.
    app.openapi_version = "3.0.3"
    app.state.container = app_container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if app_container.security_policy_service is not None:
        install_security_middleware(
            app, policy_service=app_container.security_policy_service
        )
    app.include_router(router)

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

    @app.exception_handler(AgentError)
    async def agent_error_handler(_: Request, exc: AgentError):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        payload = _validation_error_payload(exc)
        return JSONResponse(
            status_code=422,
            content=payload.to_dict(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        payload = _http_error_payload(request, exc)
        return JSONResponse(
            status_code=payload.status,
            content=payload.to_dict(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        payload = _unhandled_error_payload(request, exc)
        return JSONResponse(
            status_code=500,
            content=payload.to_dict(),
        )

    return app


def _validation_error_items(
    exc: RequestValidationError,
) -> tuple[ErrorItemPayload, ...]:
    return tuple(
        ErrorItemPayload(
            code=str(item.get("type", "validation_error")),
            detail=str(item.get("msg", "Invalid value.")),
            source=_validation_error_source(item.get("loc", ())),
            details=_validation_error_details(item),
        )
        for item in exc.errors()
    )


def _validation_error_payload(exc: RequestValidationError) -> ErrorPayload:
    return ErrorPayload(
        type="urn:inari:error:request-validation-failed",
        title="Request Validation Failed",
        status=422,
        code="REQUEST_VALIDATION_FAILED",
        detail="One or more request fields are invalid.",
        details={"error_count": len(exc.errors())},
        errors=tuple(_validation_error_items(exc)),
    )


def _validation_error_source(location: tuple[object, ...]) -> ErrorSourcePayload | None:
    if not location:
        return None

    scope = str(location[0])
    path = tuple(str(part) for part in location[1:])
    if scope == "body":
        pointer = "/" + "/".join(_escape_json_pointer(part) for part in path)
        return ErrorSourcePayload(pointer=pointer)
    if scope == "header" and path:
        return ErrorSourcePayload(header=path[0])
    if scope in {"path", "query", "cookie"} and path:
        return ErrorSourcePayload(parameter=path[0])
    return None


def _validation_error_details(item: dict[str, object]) -> dict[str, object] | None:
    details: dict[str, object] = {}
    if "ctx" in item and isinstance(item["ctx"], dict):
        details["context"] = item["ctx"]
    if "input" in item and item["input"] is not None:
        details["input_type"] = type(item["input"]).__name__
    return details or None


def _escape_json_pointer(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _http_title(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:  # pragma: no cover - defensive path
        return "HTTP Error"


def _http_error_payload(request: Request, exc: StarletteHTTPException) -> ErrorPayload:
    status_code = int(exc.status_code)
    title = _http_title(status_code)
    detail = str(exc.detail) if exc.detail else title
    return ErrorPayload(
        type=f"urn:inari:error:http-{status_code}",
        title=title,
        status=status_code,
        code=f"HTTP_{status_code}",
        detail=detail,
        details={
            "method": request.method,
            "path": request.url.path,
        },
    )


def _unhandled_error_payload(request: Request, exc: Exception) -> ErrorPayload:
    return ErrorPayload(
        type="urn:inari:error:unhandled-error",
        title="Unhandled Error",
        status=500,
        code="UNHANDLED_ERROR",
        detail="An unexpected error occurred while processing the request.",
        details={
            "exception_type": type(exc).__name__,
            "method": request.method,
            "path": request.url.path,
        },
    )


app = create_app()
