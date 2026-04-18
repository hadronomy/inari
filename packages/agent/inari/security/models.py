from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable, Mapping


class AccessScope(StrEnum):
    SYSTEM_READ = "system:read"
    DEVICES_READ = "devices:read"
    EVENTS_READ = "events:read"
    JOBS_READ = "jobs:read"
    JOBS_SUBMIT = "jobs:submit"
    COMMANDS_EXECUTE = "commands:execute"
    ADMIN_READ = "admin:read"
    ADMIN_WRITE = "admin:write"


LOCAL_OPERATOR_SCOPES = (
    AccessScope.SYSTEM_READ,
    AccessScope.DEVICES_READ,
    AccessScope.EVENTS_READ,
    AccessScope.JOBS_READ,
    AccessScope.JOBS_SUBMIT,
    AccessScope.COMMANDS_EXECUTE,
    AccessScope.ADMIN_READ,
    AccessScope.ADMIN_WRITE,
)

UPSTREAM_AGENT_SCOPES = (
    AccessScope.SYSTEM_READ,
    AccessScope.DEVICES_READ,
    AccessScope.EVENTS_READ,
    AccessScope.JOBS_READ,
    AccessScope.JOBS_SUBMIT,
    AccessScope.COMMANDS_EXECUTE,
)


class PrincipalKind(StrEnum):
    LOCAL_CLIENT = "local_client"
    API_CLIENT = "api_client"
    UPSTREAM_GATEWAY = "upstream_gateway"


class GatewayMode(StrEnum):
    STANDALONE = "standalone"
    MANAGED = "managed"


class GatewayExposure(StrEnum):
    LOOPBACK = "loopback"
    LAN = "lan"


@dataclass(slots=True, frozen=True)
class AuthenticatedPrincipal:
    subject: str
    principal_kind: PrincipalKind
    scopes: frozenset[AccessScope]
    issuer: str
    audience: str
    token_id: str | None = None
    expires_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def has_scopes(self, required: Iterable[AccessScope]) -> bool:
        return set(required).issubset(self.scopes)


@dataclass(slots=True, frozen=True)
class IssuedToken:
    access_token: str
    expires_at: datetime
    scopes: tuple[AccessScope, ...]
    subject: str
    principal_kind: PrincipalKind
    token_type: str = "Bearer"


@dataclass(slots=True, frozen=True)
class AgentIdentity:
    agent_id: str
    key_id: str
    algorithm: str
    public_jwk: Mapping[str, Any]
    created_at: datetime
    certificate_pem: str | None = None


@dataclass(slots=True, frozen=True)
class GatewaySecurityPolicy:
    mode: GatewayMode
    exposure: GatewayExposure
    require_auth: bool = True
    require_tls: bool = False
    allow_loopback_bootstrap: bool = True
    trusted_hosts: tuple[str, ...] = ()
