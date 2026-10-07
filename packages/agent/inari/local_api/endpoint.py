from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.failures import DomainFailure, ProblemCode
from .problem_handlers import problem_response


class AgentEndpointMiddleware:
    """Bind browser request targets to the operator-configured listener identity."""

    def __init__(self, app: ASGIApp, *, endpoint: str) -> None:
        self.app = app
        parsed = urlsplit(endpoint)
        assert parsed.hostname is not None
        self.scheme = parsed.scheme
        self.server = (
            parsed.hostname,
            parsed.port or (443 if self.scheme == "https" else 80),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            server = scope.get("server")
            if (
                server is None
                or server[1] != self.server[1]
                or scope["scheme"] != self.scheme
            ):
                response = problem_response(
                    DomainFailure(ProblemCode.REQUEST_MALFORMED)
                )
                await response(scope, receive, send)
                return
            # Socket addresses contain an IP, while TLS and DPoP use the configured hostname.
            # The browser boundary still requires Host to match and rejects forwarded authority.
            scope = {**scope, "server": self.server}
        await self.app(scope, receive, send)
