from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LocalTrustLevel(StrEnum):
    LOOPBACK = "loopback"
    PAIRED_BROWSER = "paired_browser"
    PAIRED_NATIVE = "paired_native"


class LocalChallengePurpose(StrEnum):
    PAIRING = "pairing"
    TOKEN = "token"


@dataclass(slots=True, frozen=True)
class LocalChallenge:
    id: str
    challenge: str
    purpose: LocalChallengePurpose
    expires_at: datetime
    client_id: str | None = None


@dataclass(slots=True, frozen=True)
class LocalClientAttestation:
    client_id: str
    challenge_id: str
    signature: str
    origin: str | None = None


@dataclass(slots=True, frozen=True)
class LocalTrustGrant:
    client_id: str | None
    client_name: str
    trust_level: LocalTrustLevel
    origin: str | None = None

    def token_claims(self) -> dict[str, str]:
        claims = {
            "client_name": self.client_name,
            "trust_level": self.trust_level.value,
        }
        if self.client_id is not None:
            claims["client_id"] = self.client_id
        if self.origin is not None:
            claims["origin"] = self.origin
        return claims


class TrustedLocalClient(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str
    client_name: str
    public_key_pem: str
    trust_level: LocalTrustLevel
    origins: list[str] = Field(default_factory=list)
    paired_at: datetime
    last_seen_at: datetime | None = None

    def allows_origin(self, origin: str | None) -> bool:
        if origin is None:
            return True
        return origin in self.origins

    def touched(self, when: datetime) -> TrustedLocalClient:
        return self.model_copy(update={"last_seen_at": when})


class LocalPairingSecret(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    secret_hash: str
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now.astimezone(UTC)


class LocalTrustState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = 1
    pairing_secret: LocalPairingSecret | None = None
    trusted_clients: list[TrustedLocalClient] = Field(default_factory=list)

    @property
    def paired(self) -> bool:
        return bool(self.trusted_clients)

    def client(self, client_id: str) -> TrustedLocalClient | None:
        return next(
            (
                client
                for client in self.trusted_clients
                if client.client_id == client_id
            ),
            None,
        )
