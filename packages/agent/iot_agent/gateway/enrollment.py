from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import AgentSettings
from ..exceptions import AgentError
from ..gateway.models import (
    CertificateBootstrapMode,
    GatewayEnrollmentRecord,
    StepCaOttBootstrap,
    UpstreamAuthMode,
    UpstreamCertificateMode,
    parse_controller_actions,
)
from ..security.certificates import CertificateLifecycleService, ManagedCertificate
from ..security.identity import AgentIdentityService
from ..security.secrets import SecretStore
from ..security.tls import TlsContextFactory
from ..version import GATEWAY_PROTOCOL_VERSION
from .auth_providers import UpstreamAuthProvider
from .protocol import (
    ControllerManagedAuthPayload,
    EnrollmentRequestPayload,
    EnrollmentResponsePayload,
    RefreshRequestPayload,
    RefreshResponsePayload,
    ZitadelServiceAccountAuthPayload,
)

UPSTREAM_ACCESS_TOKEN_KEY = "upstream_access_token"
UPSTREAM_ENROLLMENT_TOKEN_KEY = "upstream_enrollment_token"
LEGACY_UPSTREAM_BOOTSTRAP_TOKEN_KEY = "upstream_bootstrap_token"
UPSTREAM_REFRESH_TOKEN_KEY = "upstream_refresh_token"
UPSTREAM_STEP_CA_OTT_KEY = "upstream_step_ca_ott"


class GatewayEnrollmentService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        secret_store: SecretStore,
        tls_context_factory: TlsContextFactory,
        certificate_service: CertificateLifecycleService,
        auth_provider: UpstreamAuthProvider,
        metadata_path: Path,
        snapshot_provider: Callable[[], dict[str, Any]],
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.secret_store = secret_store
        self.tls_context_factory = tls_context_factory
        self.certificate_service = certificate_service
        self.auth_provider = auth_provider
        self.metadata_path = metadata_path
        self.snapshot_provider = snapshot_provider
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    async def ensure_enrolled(self) -> GatewayEnrollmentRecord | None:
        existing = self.load_enrollment()
        if (
            existing is not None
            and existing.auth_mode is UpstreamAuthMode.CONTROLLER
            and existing.access_token is None
        ):
            existing = None
        if existing is not None and not self._should_refresh(existing):
            return existing
        if (
            existing is not None
            and existing.refresh_url
            and (existing.refresh_token or existing.access_token)
        ):
            refreshed = await self._refresh(existing)
            if refreshed is not None:
                return refreshed
        enrolled = await self._enroll()
        if enrolled is None:
            return None
        return enrolled

    def load_enrollment(self) -> GatewayEnrollmentRecord | None:
        if not self.metadata_path.exists():
            return None
        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        access_token = self.secret_store.get_secret(UPSTREAM_ACCESS_TOKEN_KEY)
        refresh_token = self.secret_store.get_secret(UPSTREAM_REFRESH_TOKEN_KEY)
        bootstrap = _parse_certificate_bootstrap(
            payload.get("certificate_bootstrap"),
            ott=self.secret_store.get_secret(UPSTREAM_STEP_CA_OTT_KEY),
        )
        return GatewayEnrollmentRecord(
            access_token=access_token,
            refresh_token=refresh_token,
            enrolled_at=_parse_datetime(payload.get("enrolled_at")) or _utc_now(),
            expires_at=_parse_datetime(payload.get("expires_at")),
            token_type=str(payload.get("token_type") or "Bearer"),
            refresh_url=str(payload["refresh_url"])
            if payload.get("refresh_url")
            else None,
            status_url=str(payload["status_url"])
            if payload.get("status_url")
            else None,
            events_url=str(payload["events_url"])
            if payload.get("events_url")
            else None,
            controller_actions=parse_controller_actions(
                payload.get("controller_actions") or payload.get("granted_scopes")
            ),
            protocol_version=str(payload["protocol_version"])
            if payload.get("protocol_version")
            else None,
            controller_name=str(payload["controller_name"])
            if payload.get("controller_name")
            else None,
            controller_instance_id=(
                str(payload["controller_instance_id"])
                if payload.get("controller_instance_id")
                else None
            ),
            certificate_expires_at=_parse_datetime(
                payload.get("certificate_expires_at")
            ),
            auth_mode=UpstreamAuthMode(
                str(payload.get("auth_mode") or self.settings.upstream_auth_mode.value)
            ),
            auth_issuer=str(payload["auth_issuer"])
            if payload.get("auth_issuer")
            else None,
            auth_token_endpoint=str(payload["auth_token_endpoint"])
            if payload.get("auth_token_endpoint")
            else None,
            auth_audience=str(payload["auth_audience"])
            if payload.get("auth_audience")
            else None,
            certificate_mode=UpstreamCertificateMode(
                str(
                    payload.get("certificate_mode")
                    or self.settings.upstream_certificate_mode.value
                )
            ),
            edge_provider=self.settings.upstream_edge_provider,
            mutual_tls_mode=self.settings.upstream_mutual_tls_mode,
            certificate_bootstrap=bootstrap,
        )

    def clear_enrollment(self) -> None:
        self.secret_store.delete_secret(UPSTREAM_ACCESS_TOKEN_KEY)
        self.secret_store.delete_secret(UPSTREAM_REFRESH_TOKEN_KEY)
        self.secret_store.delete_secret(UPSTREAM_STEP_CA_OTT_KEY)
        if self.metadata_path.exists():
            self.metadata_path.unlink()

    async def _enroll(self) -> GatewayEnrollmentRecord | None:
        enrollment_url = self._enrollment_url()
        headers = await self._enrollment_headers()
        if enrollment_url is None:
            return None
        if not headers:
            return None

        identity = self.identity_service.get_or_create_identity()
        request_payload = EnrollmentRequestPayload(
            agent_id=identity.agent_id,
            key_id=identity.key_id,
            public_jwk=dict(identity.public_jwk),
            certificate_pem=identity.certificate_pem,
            csr_pem=self.identity_service.build_csr_pem(),
            snapshot=self.snapshot_provider(),
        )
        async with self._client() as client:
            response = await client.post(
                enrollment_url,
                json=request_payload.model_dump(mode="json"),
                headers=headers,
            )
            response.raise_for_status()
            body = EnrollmentResponsePayload.model_validate(response.json())
        return self._persist_record(body, fallback_agent_id=identity.agent_id)

    async def _refresh(
        self, record: GatewayEnrollmentRecord
    ) -> GatewayEnrollmentRecord | None:
        if not record.refresh_url or not record.refresh_token:
            return None
        async with self._client() as client:
            response = await client.post(
                record.refresh_url,
                json=RefreshRequestPayload(
                    selected_protocol_version=record.protocol_version
                    or GATEWAY_PROTOCOL_VERSION,
                    agent_id=self.identity_service.get_or_create_identity().agent_id,
                ).model_dump(mode="json"),
                headers={"Authorization": f"Bearer {record.refresh_token}"},
            )
            if response.status_code in {401, 403}:
                await self.auth_provider.invalidate()
                self.clear_enrollment()
                return None
            response.raise_for_status()
            body = RefreshResponsePayload.model_validate(response.json())
        return self._persist_refreshed_record(
            body,
            existing=record,
            fallback_agent_id=self.identity_service.get_or_create_identity().agent_id,
        )

    def _persist_record(
        self,
        payload: EnrollmentResponsePayload,
        *,
        fallback_agent_id: str,
    ) -> GatewayEnrollmentRecord:
        auth_mode = UpstreamAuthMode(payload.auth.mode)
        if auth_mode is not self.settings.upstream_auth_mode:
            raise AgentError(
                "UPSTREAM_AUTH_MODE_MISMATCH",
                f"The controller selected auth mode {auth_mode.value!r}, but the agent is configured for {self.settings.upstream_auth_mode.value!r}.",
                status_code=502,
            )
        if (
            payload.certificate is not None
            and payload.certificate.mode is not self.settings.upstream_certificate_mode
        ):
            raise AgentError(
                "UPSTREAM_CERTIFICATE_MODE_MISMATCH",
                f"The controller selected certificate mode {payload.certificate.mode.value!r}, but the agent is configured for {self.settings.upstream_certificate_mode.value!r}.",
                status_code=502,
            )
        auth = payload.auth
        access_token: str | None = None
        refresh_token: str | None = None
        token_type = "Bearer"
        auth_issuer: str | None = None
        auth_token_endpoint: str | None = None
        auth_audience: str | None = None
        expires_at: datetime | None = None
        if isinstance(auth, ControllerManagedAuthPayload):
            access_token = auth.access_token
            refresh_token = auth.refresh_token
            token_type = auth.token_type
            expires_at = auth.expires_at
        elif isinstance(auth, ZitadelServiceAccountAuthPayload):
            auth_issuer = auth.issuer
            auth_token_endpoint = auth.token_endpoint
            auth_audience = auth.audience

        if auth_mode is UpstreamAuthMode.CONTROLLER and not access_token:
            raise AgentError(
                "UPSTREAM_ACCESS_TOKEN_MISSING",
                "The controller did not return an access token for controller-managed upstream auth.",
                status_code=502,
            )
        if (
            self.settings.upstream_certificate_mode
            is UpstreamCertificateMode.CONTROLLER
        ):
            self.certificate_service.install(
                certificate_pem=payload.certificate.client_certificate_pem
                if payload.certificate is not None
                else None,
                ca_certificate_pem=payload.certificate.ca_certificate_pem
                if payload.certificate is not None
                else None,
            )
        certificate = self.certificate_service.current_certificate()
        bootstrap = (
            _to_step_ca_bootstrap(
                payload.certificate.bootstrap
                if payload.certificate is not None
                else None
            )
            if self.settings.upstream_certificate_mode
            is UpstreamCertificateMode.STEP_CA
            else None
        )
        record = GatewayEnrollmentRecord(
            access_token=access_token,
            refresh_token=refresh_token,
            enrolled_at=payload.enrolled_at,
            expires_at=expires_at,
            token_type=token_type,
            refresh_url=payload.links.refresh,
            status_url=payload.links.status or self._status_url(fallback_agent_id),
            events_url=payload.links.events or self._events_url(fallback_agent_id),
            controller_actions=tuple(payload.permissions.controller_actions),
            protocol_version=payload.selected_protocol_version,
            controller_name=payload.controller.name
            if payload.controller is not None
            else None,
            controller_instance_id=payload.controller.instance_id
            if payload.controller is not None
            else None,
            certificate_expires_at=certificate.not_valid_after
            if certificate is not None
            else None,
            auth_mode=auth_mode,
            auth_issuer=auth_issuer,
            auth_token_endpoint=auth_token_endpoint,
            auth_audience=auth_audience,
            certificate_mode=self.settings.upstream_certificate_mode,
            edge_provider=self.settings.upstream_edge_provider,
            mutual_tls_mode=self.settings.upstream_mutual_tls_mode,
            certificate_bootstrap=bootstrap,
        )
        self._store_enrollment(record)
        return record

    def _persist_refreshed_record(
        self,
        payload: RefreshResponsePayload,
        *,
        existing: GatewayEnrollmentRecord,
        fallback_agent_id: str,
    ) -> GatewayEnrollmentRecord:
        auth_mode = UpstreamAuthMode(payload.auth.mode)
        if auth_mode is not existing.auth_mode:
            raise AgentError(
                "UPSTREAM_AUTH_MODE_MISMATCH",
                f"The controller returned auth mode {auth_mode.value!r} during refresh, but the current enrollment uses {existing.auth_mode.value!r}.",
                status_code=502,
            )
        if not isinstance(payload.auth, ControllerManagedAuthPayload):
            raise AgentError(
                "UPSTREAM_REFRESH_MODE_INVALID",
                "Refresh returned an unsupported auth payload for controller-managed authentication.",
                status_code=502,
            )
        refreshed = GatewayEnrollmentRecord(
            access_token=payload.auth.access_token,
            refresh_token=payload.auth.refresh_token or existing.refresh_token,
            enrolled_at=existing.enrolled_at,
            expires_at=payload.auth.expires_at,
            token_type=payload.auth.token_type,
            refresh_url=payload.links.refresh or existing.refresh_url,
            status_url=payload.links.status or existing.status_url or self._status_url(fallback_agent_id),
            events_url=payload.links.events or existing.events_url or self._events_url(fallback_agent_id),
            controller_actions=existing.controller_actions,
            protocol_version=payload.selected_protocol_version or existing.protocol_version,
            controller_name=payload.controller.name
            if payload.controller is not None and payload.controller.name is not None
            else existing.controller_name,
            controller_instance_id=payload.controller.instance_id
            if payload.controller is not None and payload.controller.instance_id is not None
            else existing.controller_instance_id,
            certificate_expires_at=existing.certificate_expires_at,
            auth_mode=existing.auth_mode,
            auth_issuer=existing.auth_issuer,
            auth_token_endpoint=existing.auth_token_endpoint,
            auth_audience=existing.auth_audience,
            certificate_mode=existing.certificate_mode,
            edge_provider=existing.edge_provider,
            mutual_tls_mode=existing.mutual_tls_mode,
            certificate_bootstrap=existing.certificate_bootstrap,
        )
        self._store_enrollment(refreshed)
        return refreshed

    def persist_certificate_state(
        self,
        record: GatewayEnrollmentRecord,
        *,
        certificate: ManagedCertificate | None,
        clear_bootstrap_ott: bool,
    ) -> GatewayEnrollmentRecord:
        bootstrap = record.certificate_bootstrap
        if (
            clear_bootstrap_ott
            and certificate is not None
            and bootstrap is not None
            and bootstrap.ott is not None
        ):
            bootstrap = replace(bootstrap, ott=None)
        certificate_expires_at = (
            certificate.not_valid_after if certificate is not None else None
        )
        updated = replace(
            record,
            certificate_expires_at=certificate_expires_at,
            certificate_bootstrap=bootstrap,
        )
        self._store_enrollment(updated)
        return updated

    def _store_enrollment(self, record: GatewayEnrollmentRecord) -> None:
        if record.access_token:
            self.secret_store.set_secret(UPSTREAM_ACCESS_TOKEN_KEY, record.access_token)
        else:
            self.secret_store.delete_secret(UPSTREAM_ACCESS_TOKEN_KEY)
        if record.refresh_token:
            self.secret_store.set_secret(
                UPSTREAM_REFRESH_TOKEN_KEY, record.refresh_token
            )
        else:
            self.secret_store.delete_secret(UPSTREAM_REFRESH_TOKEN_KEY)
        if (
            record.certificate_bootstrap is not None
            and record.certificate_bootstrap.ott
        ):
            self.secret_store.set_secret(
                UPSTREAM_STEP_CA_OTT_KEY, record.certificate_bootstrap.ott
            )
        else:
            self.secret_store.delete_secret(UPSTREAM_STEP_CA_OTT_KEY)
        self._save_enrollment(record)

    def _save_enrollment(self, record: GatewayEnrollmentRecord) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        raw_payload = asdict(record)
        raw_payload.pop("access_token", None)
        raw_payload.pop("refresh_token", None)
        bootstrap_payload = raw_payload.get("certificate_bootstrap")
        if isinstance(bootstrap_payload, dict):
            bootstrap_payload.pop("ott", None)
        payload = {key: _serialize_value(value) for key, value in raw_payload.items()}
        self.metadata_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _should_refresh(self, record: GatewayEnrollmentRecord) -> bool:
        if record.auth_mode is not UpstreamAuthMode.CONTROLLER:
            return False
        if record.expires_at is None:
            return False
        remaining = (record.expires_at - _utc_now()).total_seconds()
        return remaining <= self.settings.gateway_token_refresh_skew_seconds

    async def upstream_headers(
        self, enrollment: GatewayEnrollmentRecord | None
    ) -> dict[str, str]:
        headers = await self.auth_provider.headers_for_upstream(enrollment)
        if headers:
            return headers
        if enrollment is not None and enrollment.access_token:
            return {
                "Authorization": f"{enrollment.token_type} {enrollment.access_token}"
            }
        return {}

    async def handle_auth_failure(
        self, enrollment: GatewayEnrollmentRecord | None
    ) -> None:
        await self.auth_provider.invalidate()
        if self.settings.upstream_auth_mode is UpstreamAuthMode.CONTROLLER:
            self.clear_enrollment()

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
            scheme = (
                "wss"
                if self.settings.upstream_base_url.startswith("https://")
                else "ws"
            )
            authority = self.settings.upstream_base_url.split("://", 1)[1]
            return f"{scheme}://{authority}/api/iot-agent/agents/{agent_id}/events"
        return None

    def _client(self) -> httpx.AsyncClient:
        return self._http_client_factory(
            verify=self.tls_context_factory.create_outbound_context(),
            timeout=self.settings.gateway_reconnect_delay_seconds,
        )

    async def _enrollment_headers(self) -> dict[str, str]:
        provider_headers = await self.auth_provider.headers_for_enrollment()
        if provider_headers:
            return provider_headers
        enrollment_token = (
            self.settings.upstream_enrollment_token
            or self.secret_store.get_secret(UPSTREAM_ENROLLMENT_TOKEN_KEY)
            or self.secret_store.get_secret(LEGACY_UPSTREAM_BOOTSTRAP_TOKEN_KEY)
        )
        if enrollment_token is None:
            return {}
        return {"Authorization": f"Bearer {enrollment_token}"}


def _parse_scope_values(values: object):
    from ..security.models import AccessScope

    if isinstance(values, list):
        for value in values:
            if value is not None:
                yield AccessScope(str(value))


def _parse_datetime(value: Any):
    from ..runtime.models import normalize_timestamp

    if value is None:
        return None
    return normalize_timestamp(value)


def _serialize_value(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def _parse_certificate_bootstrap(
    value: Any,
    *,
    ott: str | None,
) -> StepCaOttBootstrap | None:
    if not isinstance(value, dict):
        return None
    mode = CertificateBootstrapMode(
        str(value.get("mode") or CertificateBootstrapMode.STEP_CA_OTT.value)
    )
    if mode is not CertificateBootstrapMode.STEP_CA_OTT:
        raise AgentError(
            "UNSUPPORTED_CERTIFICATE_BOOTSTRAP",
            f"Unsupported managed certificate bootstrap mode {mode.value!r}.",
            status_code=502,
        )
    return StepCaOttBootstrap(
        mode=mode,
        ca_url=str(value["ca_url"]),
        root_fingerprint=str(value["root_fingerprint"]),
        ott=ott,
        sign_url=str(value["sign_url"]) if value.get("sign_url") else None,
        renew_url=str(value["renew_url"]) if value.get("renew_url") else None,
        expires_at=_parse_datetime(value.get("expires_at")),
        subject=str(value["subject"]) if value.get("subject") else None,
        authorized_sans=tuple(str(item) for item in value.get("authorized_sans") or ()),
        requires_mutual_tls_after_issuance=bool(
            value.get("requires_mutual_tls_after_issuance", True)
        ),
    )


def _to_step_ca_bootstrap(value: Any) -> StepCaOttBootstrap | None:
    if value is None:
        return None
    return StepCaOttBootstrap(
        mode=CertificateBootstrapMode.STEP_CA_OTT,
        ca_url=value.ca_url,
        root_fingerprint=value.root_fingerprint,
        ott=value.ott,
        sign_url=value.sign_url,
        renew_url=value.renew_url,
        expires_at=value.expires_at,
        subject=value.subject,
        authorized_sans=tuple(value.authorized_sans),
        requires_mutual_tls_after_issuance=value.requires_mutual_tls_after_issuance,
    )


def _utc_now():
    from ..runtime.models import utc_now

    return utc_now()
