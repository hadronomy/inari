from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .config import AgentSettings, get_settings
from .exceptions import AgentError
from .logging_setup import configure_logging
from .models import ErrorResponse


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, log_dir=settings.log_dir)
    yield


def create_app(settings: AgentSettings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    app = FastAPI(
        title="Odoo IoT Agent",
        version="1.1.0",
        lifespan=lifespan,
    )
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

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception):
        payload = ErrorResponse(
            ok=False,
            code="UNHANDLED_ERROR",
            message=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content=payload.model_dump(),
        )

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "iot_agent.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
