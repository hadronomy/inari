from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import AgentSettings
from ..security.identity import AgentIdentityService
from ..security.secrets import SecretStore
from ..security.tls import TlsContextFactory
from .models import GatewayEnrollmentRecord

UPSTREAM_ACCESS_TOKEN_KEY = "upstream_access_token"
UPSTREAM_BOOTSTRAP_TOKEN_KEY = "upstream_bootstrap_token"


class GatewayEnrollmentService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        secret_store: SecretStore,
        tls_context_factory: TlsContextFactory,
        metadata_path: Path,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.secret_store = secret_store
        self.tls_context_factory = tls_context_factory
        self.metadata_path = metadata_path
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    async def ensure_enrolled(self) -> GatewayEnrollmentRecord | None:
        existing = self.load_enrollment()
        if existing is not None:
            return existing

        enrollment_url = self._enrollment_url()
        bootstrap_token = self.settings.upstream_bootstrap_token or self.secret_store.get_secret(UPSTREAM_BOOTSTRAP_TOKEN_KEY)
        if enrollment_url is None or bootstrap_token is None:
            return None

        identity = self.identity_service.get_or_create_identity()
        payload = {
            "agent_id": identity.agent_id,
            "key_id": identity.key_id,
            "public_jwk": dict(identity.public_jwk),
            "certificate_pem": identity.certificate_pem,
            "csr_pem": self.identity_service.build_csr_pem(),
            "capabilities": {
                "gateway_mode": self.settings.gateway_mode.value,
                "content_transport": "https+wss",
            },
        }
        async with self._http_client_factory(
            verify=self.tls_context_factory.create_outbound_context(),
            timeout=self.settings.gateway_reconnect_delay_seconds,
        ) as client:
            response = await client.post(
                enrollment_url,
                json=payload,
                headers={"Authorization": f"Bearer {bootstrap_token}"},
            )
            response.raise_for_status()
            body = response.json()
        record = GatewayEnrollmentRecord(
            access_token=str(body["access_token"]),
            enrolled_at=_parse_datetime(body.get("enrolled_at")) or _utc_now(),
            expires_at=_parse_datetime(body.get("expires_at")),
            status_url=str(body["status_url"]) if body.get("status_url") else self._status_url(identity.agent_id),
            events_url=str(body["events_url"]) if body.get("events_url") else self._events_url(identity.agent_id),
        )
        self._save_enrollment(record)
        self.secret_store.set_secret(UPSTREAM_ACCESS_TOKEN_KEY, record.access_token)
        return record

    def load_enrollment(self) -> GatewayEnrollmentRecord | None:
        access_token = self.secret_store.get_secret(UPSTREAM_ACCESS_TOKEN_KEY)
        if access_token is None or not self.metadata_path.exists():
            return None
        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        return GatewayEnrollmentRecord(
            access_token=access_token,
            enrolled_at=_parse_datetime(payload.get("enrolled_at")) or _utc_now(),
            expires_at=_parse_datetime(payload.get("expires_at")),
            status_url=str(payload["status_url"]) if payload.get("status_url") else None,
            events_url=str(payload["events_url"]) if payload.get("events_url") else None,
        )

    def clear_enrollment(self) -> None:
        self.secret_store.delete_secret(UPSTREAM_ACCESS_TOKEN_KEY)
        if self.metadata_path.exists():
            self.metadata_path.unlink()

    def _save_enrollment(self, record: GatewayEnrollmentRecord) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in asdict(record).items()
            if key != "access_token"
        }
        self.metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _enrollment_url(self) -> str | None:
        if self.settings.upstream_enrollment_url:
            return self.settings.upstream_enrollment_url
        if self.settings.upstream_base_url:
            return f"{self.settings.upstream_base_url}/api/iot-agent/enroll"
        return None

    def _status_url(self, agent_id: str) -> str | None:
        if self.settings.upstream_status_url:
            return self.settings.upstream_status_url.format(agent_id=agent_id)
        if self.settings.upstream_base_url:
            return f"{self.settings.upstream_base_url}/api/iot-agent/agents/{agent_id}/status"
        return None

    def _events_url(self, agent_id: str) -> str | None:
        if self.settings.upstream_events_url:
            return self.settings.upstream_events_url.format(agent_id=agent_id)
        if self.settings.upstream_base_url:
            scheme = "wss" if self.settings.upstream_base_url.startswith("https://") else "ws"
            authority = self.settings.upstream_base_url.split("://", 1)[1]
            return f"{scheme}://{authority}/api/iot-agent/agents/{agent_id}/events"
        return None


def _parse_datetime(value: Any):
    from ..runtime.models import normalize_timestamp

    if value is None:
        return None
    return normalize_timestamp(value)


def _utc_now():
    from ..runtime.models import utc_now

    return utc_now()
