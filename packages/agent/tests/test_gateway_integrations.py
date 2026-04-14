from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from iot_agent.config import AgentSettings
from iot_agent.gateway.auth_providers import ZitadelServiceAccountAuthProvider
from iot_agent.gateway.caddy import CaddyControllerProfile
from iot_agent.gateway.enrollment import GatewayEnrollmentService, UPSTREAM_STEP_CA_OTT_KEY
from iot_agent.gateway.models import CertificateBootstrapMode, GatewayEnrollmentRecord, MutualTlsMode, StepCaOttBootstrap
from iot_agent.security.certificate_provisioners import StepCaOttCertificateProvisioner
from iot_agent.security.certificates import CertificateLifecycleService
from iot_agent.security.identity import AgentIdentityService
from iot_agent.security.policies import SecurityPolicyService
from iot_agent.security.secrets import MemorySecretStore
from iot_agent.security.tls import TlsContextFactory
from iot_agent.version import API_VERSION, GATEWAY_PROTOCOL_VERSION


@pytest.mark.anyio
async def test_zitadel_auth_provider_exchanges_and_caches_token(tmp_path: Path) -> None:
    key_path = tmp_path / "zitadel-service-account.json"
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_text(
        json.dumps(
            {
                "keyId": "sa-key-1",
                "userId": "service-user-1",
                "key": rsa_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode("utf-8"),
            }
        ),
        encoding="utf-8",
    )
    fake_client = FakeAsyncHttpClient(
        response_payload={
            "access_token": "zitadel-token",
            "token_type": "Bearer",
            "expires_in": 300,
        }
    )
    provider = ZitadelServiceAccountAuthProvider(
        settings=AgentSettings(
            upstream_auth_mode="zitadel_service_account",
            zitadel_base_url="https://zitadel.example.com",
            zitadel_service_account_key_path=key_path,
            zitadel_requested_scopes=["openid", "events:read"],
        ),
        http_client_factory=lambda **kwargs: fake_client,
    )

    first = await provider.headers_for_enrollment()
    second = await provider.headers_for_upstream(None)

    assert first["Authorization"] == "Bearer zitadel-token"
    assert second["Authorization"] == "Bearer zitadel-token"
    assert fake_client.post_calls == 1
    assert fake_client.last_post_data["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert "openid" in fake_client.last_post_data["scope"]


@pytest.mark.anyio
async def test_enrollment_can_be_authorized_by_provider_without_controller_token(tmp_path: Path) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    http_client = FakeAsyncHttpClient(
        response_payload={
            "protocol_version": GATEWAY_PROTOCOL_VERSION,
            "controller_name": "Controller",
            "enrolled_at": "2026-04-12T00:00:00Z",
            "status_url": "https://controller.example.com/status",
            "events_url": "wss://controller.example.com/events",
            "granted_scopes": ["jobs:submit"],
        }
    )
    service = GatewayEnrollmentService(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_auth_mode="zitadel_service_account",
        ),
        identity_service=identity_service,
        secret_store=MemorySecretStore(),
        tls_context_factory=TlsContextFactory(AgentSettings()),
        certificate_service=certificate_service,
        auth_provider=StaticAuthProvider({"Authorization": "Bearer zitadel-token"}),
        certificate_provisioner=NoopCertificateProvisioner(),
        metadata_path=tmp_path / "upstream-enrollment.json",
        snapshot_provider=_gateway_snapshot_payload,
        http_client_factory=lambda **kwargs: http_client,
    )

    record = await service.ensure_enrolled()

    assert record is not None
    assert record.access_token is None
    assert http_client.last_post_headers["Authorization"] == "Bearer zitadel-token"


@pytest.mark.anyio
async def test_enrollment_includes_code_and_persists_step_ca_bootstrap_without_metadata_ott(tmp_path: Path) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    secret_store = MemorySecretStore()
    http_client = FakeAsyncHttpClient(
        response_payload={
            "protocol_version": GATEWAY_PROTOCOL_VERSION,
            "controller_name": "Controller",
            "access_token": "controller-token",
            "enrolled_at": "2026-04-13T00:00:00Z",
            "status_url": "https://controller.example.com/status",
            "events_url": "wss://controller.example.com/events",
            "granted_scopes": ["jobs:submit"],
            "certificate_bootstrap": {
                "mode": "step_ca_ott",
                "ca_url": "https://step-ca.example.com",
                "root_fingerprint": "0123abcd",
                "ott": "ott_bootstrap_token",
                "sign_url": "https://step-ca.example.com/1.0/sign",
                "renew_url": "https://step-ca.example.com/1.0/renew",
                "subject": "agt_test",
                "authorized_sans": ["urn:iot-agent:agt_test"],
                "requires_mutual_tls_after_issuance": True,
            },
        }
    )
    provisioner = NoopCertificateProvisioner()
    service = GatewayEnrollmentService(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_certificate_mode="step_ca",
            upstream_enrollment_code="ABCD-1234",
        ),
        identity_service=identity_service,
        secret_store=secret_store,
        tls_context_factory=TlsContextFactory(AgentSettings()),
        certificate_service=certificate_service,
        auth_provider=StaticAuthProvider({"Authorization": "Bearer bootstrap-token"}),
        certificate_provisioner=provisioner,
        metadata_path=tmp_path / "upstream-enrollment.json",
        snapshot_provider=_gateway_snapshot_payload,
        http_client_factory=lambda **kwargs: http_client,
    )

    record = await service.ensure_enrolled()

    assert record is not None
    assert http_client.last_post_json["enrollment_code"] == "ABCD-1234"
    assert record.certificate_bootstrap is not None
    assert record.certificate_bootstrap.mode is CertificateBootstrapMode.STEP_CA_OTT
    assert record.certificate_bootstrap.ott == "ott_bootstrap_token"
    assert secret_store.get_secret(UPSTREAM_STEP_CA_OTT_KEY) == "ott_bootstrap_token"
    metadata = json.loads((tmp_path / "upstream-enrollment.json").read_text(encoding="utf-8"))
    assert "certificate_bootstrap" in metadata
    assert "ott" not in metadata["certificate_bootstrap"]

    reloaded = service.load_enrollment()
    assert reloaded is not None
    assert reloaded.certificate_bootstrap is not None
    assert reloaded.certificate_bootstrap.ott == "ott_bootstrap_token"


@pytest.mark.anyio
async def test_step_ca_ott_provisioner_bootstraps_root_and_issues_certificate(tmp_path: Path) -> None:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _issue_certificate(
        subject_name="Example Step CA",
        issuer_name="Example Step CA",
        subject_key=ca_key.public_key(),
        issuer_key=ca_key,
        not_valid_after=datetime.now(tz=UTC) + timedelta(days=365),
        is_ca=True,
    )
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    identity_path = tmp_path / "identity.pem"
    identity_service = AgentIdentityService(identity_path=identity_path)
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=identity_path,
        ca_path=tmp_path / "upstream-ca.pem",
    )
    sign_client = StepCaHttpClient(
        root_pem=ca_pem,
        ca_key=ca_key,
        certificate_service=certificate_service,
    )
    provisioner = StepCaOttCertificateProvisioner(
        settings=AgentSettings(
            upstream_certificate_mode="step_ca",
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(ca_cert),
        ),
        identity_service=identity_service,
        certificate_service=certificate_service,
        http_client_factory=lambda **kwargs: sign_client,
    )
    enrollment = _enrollment_record(
        certificate_bootstrap=StepCaOttBootstrap(
            mode=CertificateBootstrapMode.STEP_CA_OTT,
            ca_url="https://step-ca.example.com",
            root_fingerprint=_fingerprint(ca_cert),
            ott="ott_bootstrap_token",
            sign_url="https://step-ca.example.com/1.0/sign",
            renew_url="https://step-ca.example.com/1.0/renew",
            subject="agt_test",
            authorized_sans=("urn:iot-agent:agt_test",),
            requires_mutual_tls_after_issuance=True,
        )
    )

    certificate = await provisioner.ensure_certificate(enrollment)

    assert certificate is not None
    assert (tmp_path / "upstream-ca.pem").exists()
    assert (tmp_path / "upstream-client-cert.pem").exists()
    assert sign_client.sign_calls == 1


def test_caddy_required_mtls_requires_enrollment_route_or_existing_certificate(tmp_path: Path) -> None:
    settings = AgentSettings(
        gateway_mode="managed",
        upstream_base_url="https://controller.example.com",
        upstream_edge_provider="caddy",
        upstream_mutual_tls_mode=MutualTlsMode.REQUIRED,
        security_state_dir=tmp_path,
    )

    with pytest.raises(RuntimeError):
        SecurityPolicyService(settings).validate_startup()


def test_zitadel_requires_key_material() -> None:
    settings = AgentSettings(
        gateway_mode="managed",
        upstream_base_url="https://controller.example.com",
        upstream_auth_mode="zitadel_service_account",
        zitadel_base_url="https://zitadel.example.com",
    )

    with pytest.raises(RuntimeError):
        SecurityPolicyService(settings).validate_startup()


def test_caddy_profile_renders_required_client_auth_snippet() -> None:
    profile = CaddyControllerProfile.from_settings(
        AgentSettings(
            upstream_edge_provider="caddy",
            upstream_mutual_tls_mode="required",
        )
    )

    rendered = profile.render_example()

    assert "client_auth" in rendered
    assert "require_and_verify" in rendered


class StaticAuthProvider:
    mode = "static"

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers

    async def headers_for_enrollment(self) -> dict[str, str]:
        return dict(self.headers)

    async def headers_for_upstream(self, enrollment) -> dict[str, str]:
        return dict(self.headers)

    async def invalidate(self) -> None:
        return None


class NoopCertificateProvisioner:
    mode = "none"

    async def ensure_certificate(self, enrollment=None):
        return None


class FakeAsyncHttpClient:
    def __init__(self, response_payload: dict[str, object]) -> None:
        self.response_payload = response_payload
        self.post_calls = 0
        self.last_post_headers: dict[str, str] = {}
        self.last_post_data: dict[str, str] = {}
        self.last_post_json: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, data: dict[str, str] | None = None, json: dict[str, Any] | None = None, headers=None):
        self.post_calls += 1
        self.last_post_headers = dict(headers or {})
        if data is not None:
            self.last_post_data = dict(data)
        self.last_post_json = dict(json or {})
        return FakeAsyncResponse(self.response_payload)


class StepCaHttpClient:
    def __init__(
        self,
        *,
        root_pem: str,
        ca_key,
        certificate_service: CertificateLifecycleService,
    ) -> None:
        self.root_pem = root_pem
        self.ca_key = ca_key
        self.certificate_service = certificate_service
        self.sign_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str):
        return FakeTextResponse(self.root_pem)

    async def post(self, url: str, *, json: dict[str, str] | None = None, headers=None):
        if url.endswith("/sign"):
            self.sign_calls += 1
            assert json is not None
            csr = x509.load_pem_x509_csr(json["csr"].encode("utf-8"))
            certificate = _issue_certificate_from_csr(csr, self.ca_key)
            return FakeAsyncResponse(
                {
                    "crt": certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8"),
                    "ca": self.root_pem,
                }
            )
        certificate_path, _, _ = self.certificate_service.current_cert_chain()
        current_cert = Path(certificate_path).read_text(encoding="utf-8") if certificate_path else self.root_pem
        return FakeAsyncResponse({"crt": current_cert, "ca": self.root_pem})


class FakeAsyncResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.status_code = 200
        self.content = json.dumps(payload).encode("utf-8") if payload else b""
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return dict(self.payload)


class FakeTextResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200
        self.content = text.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


def _issue_certificate(
    *,
    subject_name: str,
    issuer_name: str,
    subject_key,
    issuer_key,
    not_valid_after: datetime,
    is_ca: bool,
):
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(subject_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(minutes=1))
        .not_valid_after(not_valid_after)
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    )
    return builder.sign(issuer_key, hashes.SHA256())


def _issue_certificate_from_csr(csr: x509.CertificateSigningRequest, issuer_key):
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example Step CA")]))
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(tz=UTC) + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    )
    for extension in csr.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    return builder.sign(issuer_key, hashes.SHA256())


def _fingerprint(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex()


def _enrollment_record(
    *,
    certificate_bootstrap: StepCaOttBootstrap | None = None,
) -> GatewayEnrollmentRecord:
    return GatewayEnrollmentRecord(
        access_token="controller-token",
        enrolled_at=datetime.now(tz=UTC),
        status_url="https://controller.example.com/status",
        events_url="wss://controller.example.com/events",
        protocol_version=GATEWAY_PROTOCOL_VERSION,
        certificate_bootstrap=certificate_bootstrap,
    )


def _gateway_snapshot_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-04-13T00:00:00Z",
        "protocol": {
            "version": GATEWAY_PROTOCOL_VERSION,
            "supported_versions": [GATEWAY_PROTOCOL_VERSION],
        },
        "service": {
            "name": "IoT Agent",
            "version": API_VERSION,
            "agent_id": "agt_test",
            "key_id": "kid_test",
        },
        "security": {
            "mode": "managed",
            "exposure": "loopback",
            "tls_required": False,
            "edge_provider": "direct",
            "auth_mode": "controller",
            "certificate_mode": "controller",
            "mutual_tls_mode": "disabled",
            "mutual_tls_enabled": False,
            "certificate_expires_at": None,
        },
        "runtime": {
            "queue": {
                "total": 0,
                "queued": 0,
                "dispatched": 0,
                "running": 0,
                "retry_scheduled": 0,
                "succeeded": 0,
                "failed": 0,
                "cancelled": 0,
            },
            "devices": {
                "count": 0,
                "online_count": 0,
                "offline_count": 0,
                "kind_counts": {},
                "default_device_id": None,
                "default_device_name": None,
            },
        },
        "capabilities": {
            "supported_content_kinds": ["text"],
            "supported_device_commands": ["cut_paper"],
            "granted_scopes": ["jobs:submit"],
            "features": ["status_sync"],
            "transport": "https+wss",
            "client_certificate_present": False,
        },
        "observability": {},
    }
