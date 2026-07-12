from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from ...config import AgentSettings
from ...core.exceptions import AgentError
from ...core.version import SUPPORTED_GATEWAY_PROTOCOL_VERSIONS
from ...security.certificates.store import (
    CertificateLifecycleService,
    ManagedCertificate,
)
from ...security.identity import AgentIdentityService
from ...security.secrets import SecretStore
from ...security.files import write_text_owner_only
from ...security.tls import TlsContextFactory
from ..models import (
    CertificateBootstrapAuth,
    CertificateBootstrapAuthType,
    CertificateEnrollmentSpec,
    CertificateTrustSpec,
    GatewayEnrollmentRecord,
    UpstreamCertificateMode,
    UpstreamDataPlaneKind,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
    parse_controller_actions,
)
from ..protocol import (
    ControllerCertificatePayload,
    EnrollmentRequestPayload,
    EnrollmentResponsePayload,
    GatewaySnapshotPayload,
    StepCaCertificateEnrollmentPayload,
    StepCaCertificatePayload,
)
from .auth import UpstreamAuthProvider

UPSTREAM_ENROLLMENT_TOKEN_KEY = "upstream_enrollment_token"
LEGACY_UPSTREAM_BOOTSTRAP_TOKEN_KEY = "upstream_bootstrap_token"
UPSTREAM_CERTIFICATE_BOOTSTRAP_TOKEN_KEY = "upstream_certificate_bootstrap_token"


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
        snapshot_provider: Callable[[], GatewaySnapshotPayload],
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
        if existing is not None:
            return existing
        return await self._enroll()

    def load_enrollment(self) -> GatewayEnrollmentRecord | None:
        if not self.metadata_path.exists():
            return None
        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        data_plane_payload = payload.get("data_plane")
        if not isinstance(data_plane_payload, dict):
            return None
        connect_endpoints = tuple(
            str(item)
            for item in (
                data_plane_payload.get("connect_endpoints")
                or self.settings.zenoh_connect_endpoints
            )
            or ()
        )
        namespace = str(
            data_plane_payload.get("namespace") or self.settings.zenoh_namespace or ""
        ).strip()
        if not connect_endpoints or not namespace:
            return None
        certificate_enrollment = _parse_certificate_enrollment(
            payload.get("certificate_enrollment"),
            bootstrap_token=self.secret_store.get_secret(
                UPSTREAM_CERTIFICATE_BOOTSTRAP_TOKEN_KEY
            ),
        )
        certificate_mode = UpstreamCertificateMode(
            str(
                payload.get("certificate_mode")
                or self.settings.upstream_certificate_mode.value
            )
        )
        return GatewayEnrollmentRecord(
            enrolled_at=_parse_datetime(payload.get("enrolled_at")) or _utc_now(),
            data_plane=ZenohDataPlaneConfig(
                kind=UpstreamDataPlaneKind.ZENOH,
                session_mode=ZenohSessionMode(
                    str(
                        data_plane_payload.get("session_mode")
                        or self.settings.zenoh_session_mode.value
                    )
                ),
                connect_endpoints=connect_endpoints,
                namespace=namespace,
                serialization=ZenohSerialization(
                    str(
                        data_plane_payload.get("serialization")
                        or ZenohSerialization.JSON.value
                    )
                ),
                auth_kind=ZenohDataPlaneAuthKind(
                    str(
                        (data_plane_payload.get("auth") or {}).get("kind")
                        or ZenohDataPlaneAuthKind.MTLS.value
                    )
                ),
                close_link_on_expiration=bool(
                    (data_plane_payload.get("tls") or {}).get(
                        "close_link_on_expiration",
                        self.settings.zenoh_close_link_on_expiration,
                    )
                ),
            ),
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
            certificate_mode=certificate_mode,
            edge_provider=self.settings.upstream_edge_provider,
            mutual_tls_mode=self.settings.upstream_mutual_tls_mode,
            certificate_enrollment=certificate_enrollment,
        )

    def clear_enrollment(self) -> None:
        self.secret_store.delete_secret(UPSTREAM_CERTIFICATE_BOOTSTRAP_TOKEN_KEY)
        if self.metadata_path.exists():
            self.metadata_path.unlink()

    async def handle_auth_failure(
        self, enrollment: GatewayEnrollmentRecord | None = None
    ) -> None:
        del enrollment
        await self.auth_provider.invalidate()
        self.clear_enrollment()

    async def _enroll(self) -> GatewayEnrollmentRecord | None:
        enrollment_url = self._enrollment_url()
        headers = await self._enrollment_headers()
        if enrollment_url is None or not headers:
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

    def _persist_record(
        self,
        payload: EnrollmentResponsePayload,
        *,
        fallback_agent_id: str,
    ) -> GatewayEnrollmentRecord:
        data_plane = payload.data_plane
        if payload.selected_protocol_version not in SUPPORTED_GATEWAY_PROTOCOL_VERSIONS:
            raise AgentError(
                "UNSUPPORTED_GATEWAY_PROTOCOL_VERSION",
                f"The controller selected unsupported gateway protocol version {payload.selected_protocol_version!r}.",
                status_code=502,
            )
        if data_plane.kind is not UpstreamDataPlaneKind.ZENOH:
            raise AgentError(
                "UNSUPPORTED_DATA_PLANE",
                f"Unsupported managed data plane {data_plane.kind.value!r}.",
                status_code=502,
            )
        if (
            self.settings.upstream_certificate_mode is not UpstreamCertificateMode.NONE
            and payload.certificate is None
        ):
            raise AgentError(
                "UPSTREAM_CERTIFICATE_REQUIRED",
                "The controller did not return managed certificate details for the Zenoh data plane.",
                status_code=502,
            )
        certificate_enrollment = None
        certificate_payload = payload.certificate
        if certificate_payload is not None:
            if certificate_payload.mode is not self.settings.upstream_certificate_mode:
                raise AgentError(
                    "UPSTREAM_CERTIFICATE_MODE_MISMATCH",
                    f"The controller selected certificate mode {certificate_payload.mode.value!r}, but the agent is configured for {self.settings.upstream_certificate_mode.value!r}.",
                    status_code=502,
                )
            match certificate_payload:
                case ControllerCertificatePayload():
                    self.certificate_service.install(
                        certificate_pem=certificate_payload.client_certificate_pem,
                        ca_certificate_pem=certificate_payload.ca_certificate_pem,
                    )
                case StepCaCertificatePayload():
                    certificate_enrollment = _to_certificate_enrollment_spec(
                        certificate_payload.enrollment
                    )

        certificate = self.certificate_service.current_certificate()
        record = GatewayEnrollmentRecord(
            enrolled_at=payload.enrolled_at,
            data_plane=ZenohDataPlaneConfig(
                kind=UpstreamDataPlaneKind.ZENOH,
                session_mode=data_plane.session_mode,
                connect_endpoints=self._resolve_connect_endpoints(data_plane),
                namespace=self._resolve_namespace(data_plane, fallback_agent_id),
                serialization=data_plane.serialization,
                auth_kind=data_plane.auth.kind,
                close_link_on_expiration=(
                    self.settings.zenoh_close_link_on_expiration
                    if self.settings.zenoh_close_link_on_expiration
                    != data_plane.tls.close_link_on_expiration
                    and self.settings.zenoh_connect_endpoints
                    else data_plane.tls.close_link_on_expiration
                ),
            ),
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
            certificate_mode=self.settings.upstream_certificate_mode,
            edge_provider=self.settings.upstream_edge_provider,
            mutual_tls_mode=self.settings.upstream_mutual_tls_mode,
            certificate_enrollment=certificate_enrollment,
        )
        self._store_enrollment(record)
        self.secret_store.delete_secret(UPSTREAM_ENROLLMENT_TOKEN_KEY)
        self.secret_store.delete_secret(LEGACY_UPSTREAM_BOOTSTRAP_TOKEN_KEY)
        return record

    def persist_certificate_state(
        self,
        record: GatewayEnrollmentRecord,
        *,
        certificate: ManagedCertificate | None,
        clear_bootstrap_auth: bool,
    ) -> GatewayEnrollmentRecord:
        updated = replace(
            record,
            certificate_expires_at=(
                certificate.not_valid_after if certificate is not None else None
            ),
        )
        if clear_bootstrap_auth and certificate is not None:
            updated = updated.clear_bootstrap_token()
        self._store_enrollment(updated)
        return updated

    def _store_enrollment(self, record: GatewayEnrollmentRecord) -> None:
        bootstrap_auth = (
            record.certificate_enrollment.bootstrap_auth
            if record.certificate_enrollment is not None
            else None
        )
        if bootstrap_auth is not None and bootstrap_auth.token:
            self.secret_store.set_secret(
                UPSTREAM_CERTIFICATE_BOOTSTRAP_TOKEN_KEY,
                bootstrap_auth.token,
            )
        else:
            self.secret_store.delete_secret(UPSTREAM_CERTIFICATE_BOOTSTRAP_TOKEN_KEY)
        self._save_enrollment(record)

    def _save_enrollment(self, record: GatewayEnrollmentRecord) -> None:
        raw_payload = record.to_persisted_dict()
        payload = {key: _serialize_value(value) for key, value in raw_payload.items()}
        write_text_owner_only(
            self.metadata_path,
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _resolve_connect_endpoints(self, data_plane) -> tuple[str, ...]:
        if self.settings.zenoh_connect_endpoints:
            return tuple(self.settings.zenoh_connect_endpoints)
        endpoints = tuple(str(item) for item in data_plane.connect_endpoints)
        if endpoints:
            return endpoints
        raise AgentError(
            "DATA_PLANE_ENDPOINTS_MISSING",
            "The controller did not return any Zenoh connect endpoints and no local override is configured.",
            status_code=502,
        )

    def _resolve_namespace(self, data_plane, fallback_agent_id: str) -> str:
        if self.settings.zenoh_namespace:
            return self.settings.zenoh_namespace
        if data_plane.namespace:
            return data_plane.namespace
        return f"iot/v1/agents/{fallback_agent_id}"

    def _enrollment_url(self) -> str | None:
        if self.settings.upstream_enrollment_url:
            return self.settings.upstream_enrollment_url
        if self.settings.upstream_base_url:
            return f"{self.settings.upstream_base_url}/api/inari/v1/enrollments"
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


def _parse_datetime(value: Any):
    from ...runtime.models import normalize_timestamp

    if value is None:
        return None
    return normalize_timestamp(value)


def _serialize_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _parse_certificate_enrollment(
    value: Any,
    *,
    bootstrap_token: str | None,
) -> CertificateEnrollmentSpec | None:
    if value is None:
        return None
    try:
        payload = StepCaCertificateEnrollmentPayload.model_validate(value)
    except ValidationError as exc:
        raise AgentError(
            "CERTIFICATE_ENROLLMENT_METADATA_INVALID",
            "Persisted managed certificate enrollment metadata is invalid.",
            status_code=500,
        ) from exc
    return _to_certificate_enrollment_spec(payload, bootstrap_token=bootstrap_token)


def _to_certificate_enrollment_spec(
    value: StepCaCertificateEnrollmentPayload | None,
    *,
    bootstrap_token: str | None = None,
) -> CertificateEnrollmentSpec | None:
    if value is None:
        return None
    trust = None
    if value.trust is not None:
        trust = CertificateTrustSpec(root_fingerprint=value.trust.root_fingerprint)
    bootstrap_auth = None
    if value.bootstrap_auth is not None:
        bootstrap_auth = CertificateBootstrapAuth(
            type=CertificateBootstrapAuthType(value.bootstrap_auth.type),
            token=bootstrap_token
            if bootstrap_token is not None
            else value.bootstrap_auth.token,
            expires_at=value.bootstrap_auth.expires_at,
        )
    return CertificateEnrollmentSpec(
        base_url=value.base_url,
        trust=trust,
        bootstrap_auth=bootstrap_auth,
        subject=value.subject,
        authorized_sans=tuple(value.authorized_sans),
        requires_mutual_tls_after_issuance=value.requires_mutual_tls_after_issuance,
    )


def _utc_now():
    from ...runtime.models import utc_now

    return utc_now()
