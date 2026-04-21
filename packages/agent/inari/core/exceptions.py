from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(slots=True, frozen=True)
class ErrorSourcePayload:
    pointer: str | None = None
    parameter: str | None = None
    header: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "pointer": self.pointer,
                "parameter": self.parameter,
                "header": self.header,
            }.items()
            if value is not None
        }


@dataclass(slots=True, frozen=True)
class ErrorItemPayload:
    code: str
    detail: str
    source: ErrorSourcePayload | None = None
    details: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "detail": self.detail,
        }
        if self.source is not None:
            payload["source"] = self.source.to_dict()
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(slots=True, frozen=True)
class ErrorPayload:
    type: str
    title: str
    status: int
    code: str
    detail: str
    ok: bool = False
    source: ErrorSourcePayload | None = None
    details: Mapping[str, object] | None = None
    errors: tuple[ErrorItemPayload, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "code": self.code,
            "detail": self.detail,
        }
        if self.source is not None:
            payload["source"] = self.source.to_dict()
        if self.details:
            payload["details"] = dict(self.details)
        if self.errors:
            payload["errors"] = [error.to_dict() for error in self.errors]
        return payload


class AgentError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        title: str | None = None,
        type_uri: str | None = None,
        source: ErrorSourcePayload | None = None,
        details: Mapping[str, object] | None = None,
        errors: tuple[ErrorItemPayload, ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.title = title or _title_from_code(code)
        self.type_uri = type_uri or _type_uri_from_code(code)
        self.source = source
        self.details = dict(details) if details else None
        self.errors = tuple(errors)

    def to_payload(self) -> ErrorPayload:
        return ErrorPayload(
            type=self.type_uri,
            title=self.title,
            status=self.status_code,
            code=self.code,
            detail=self.message,
            source=self.source,
            details=self.details,
            errors=self.errors,
        )

    def to_dict(self) -> dict[str, object]:
        return self.to_payload().to_dict()


class PrinterServiceError(AgentError):
    pass


def _title_from_code(code: str) -> str:
    acronym_map = {
        "api": "API",
        "escpos": "ESC/POS",
        "html": "HTML",
        "http": "HTTP",
        "iot": "IoT",
        "mime": "MIME",
        "pdf": "PDF",
        "raw": "RAW",
    }
    words = []
    for token in code.lower().split("_"):
        words.append(acronym_map.get(token, token.capitalize()))
    return " ".join(words)


def _type_uri_from_code(code: str) -> str:
    slug = code.strip().lower().replace("_", "-")
    return f"urn:inari:error:{slug}"
