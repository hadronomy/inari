from __future__ import annotations

from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import API_VERSION, SERVICE_NAME, router
from .container import AgentContainer, build_container, get_default_container
from .config import AgentSettings, get_settings
from .exceptions import AgentError, ErrorItemPayload, ErrorPayload, ErrorSourcePayload
from .logging_setup import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    container: AgentContainer = app.state.container
    configure_logging(container.settings.log_level, log_dir=container.settings.log_dir)
    await container.runtime_supervisor.start()
    try:
        yield
    finally:
        await container.runtime_supervisor.stop()


def create_app(settings: AgentSettings | None = None, *, container: AgentContainer | None = None) -> FastAPI:
    app_container = container or (build_container(settings) if settings is not None else get_default_container())
    app_settings = app_container.settings
    app = FastAPI(
        title=SERVICE_NAME,
        version=API_VERSION,
        lifespan=lifespan,
    )
    app.state.container = app_container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

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


def _validation_error_items(exc: RequestValidationError) -> tuple[ErrorItemPayload, ...]:
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
        type="urn:iot-agent:error:request-validation-failed",
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
        type=f"urn:iot-agent:error:http-{status_code}",
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
        type="urn:iot-agent:error:unhandled-error",
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


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "iot_agent.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
