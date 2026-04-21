from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import APIModel


class ErrorSourceResponse(APIModel):
    pointer: str | None = None
    parameter: str | None = None
    header: str | None = None


class ErrorItemResponse(APIModel):
    code: str
    detail: str
    source: ErrorSourceResponse | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(APIModel):
    ok: Literal[False] = False
    type: str
    title: str
    status: int
    code: str
    detail: str
    source: ErrorSourceResponse | None = None
    details: dict[str, Any] | None = None
    errors: list[ErrorItemResponse] = Field(default_factory=list)
