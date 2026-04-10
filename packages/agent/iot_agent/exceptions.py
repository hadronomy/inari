from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True, frozen=True)
class ErrorPayload:
    code: str
    message: str
    ok: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AgentError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_payload(self) -> ErrorPayload:
        return ErrorPayload(code=self.code, message=self.message)

    def to_dict(self) -> dict[str, object]:
        return self.to_payload().to_dict()


class PrinterServiceError(AgentError):
    pass