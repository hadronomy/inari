from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from ...config import AgentSettings

WINDOWS_PAIRING_PIPE = r"\\.\pipe\Inari.Agent.Pairing"


class NativePairingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_secret: str
    expires_at: datetime


class NativeEndpointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_endpoint: str


def native_agent_endpoint(settings: AgentSettings) -> str:
    tls_enabled = (
        settings.tls_cert_path is not None and settings.tls_key_path is not None
    )
    endpoint = settings.agent_endpoint
    if endpoint is None:
        if tls_enabled:
            raise ValueError(
                "Set api.endpoint to the HTTPS certificate hostname and listener port."
            )
        endpoint = f"http://127.0.0.1:{settings.port}/"
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != ("https" if tls_enabled else "http")
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (parsed.port or (443 if tls_enabled else 80)) != settings.port
        or parsed.hostname not in settings.trusted_hosts
        or (
            not tls_enabled and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise ValueError(
            "api.endpoint must name the trusted local listener without credentials, a path, query, or fragment."
        )
    return endpoint.rstrip("/") + "/"
