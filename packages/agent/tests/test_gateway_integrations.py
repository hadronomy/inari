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

from inari.config import AgentSettings
from inari.gateway.auth_providers import ZitadelServiceAccountAuthProvider
from inari.gateway.caddy import CaddyControllerProfile
from inari.gateway.enrollment import (
    GatewayEnrollmentService,
    UPSTREAM_STEP_CA_OTT_KEY,
)
from inari.gateway.models import (
    CertificateBootstrapMode,
    GatewayEnrollmentRecord,
    MutualTlsMode,
    StepCaOttBootstrap,
    UpstreamCertificateMode,
    UpstreamDataPlaneKind,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
    resolve_mutual_tls_policy,
)
from inari.security.certificate_lifecycle import ManagedCertificateLifecycleManager
from inari.security.certificate_provisioners import StepCaOttCertificateProvisioner
from inari.security.certificates import CertificateLifecycleService
from inari.security.identity import AgentIdentityService
from inari.security.policies import SecurityPolicyService
from inari.security.secrets import MemorySecretStore
from inari.security.tls import TlsContextFactory
from inari.version import API_VERSION, GATEWAY_PROTOCOL_VERSION


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
    assert first["Authorization"] == "Bearer zitadel-token"
    assert fake_client.post_calls == 1
    assert (
        fake_client.last_post_data["grant_type"]
        == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    )
    assert "openid" in fake_client.last_post_data["scope"]


@pytest.mark.anyio
async def test_enrollment_can_be_authorized_by_provider_without_controller_token(
    tmp_path: Path,
) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    http_client = FakeAsyncHttpClient(
        response_payload=_enrollment_response_payload(
            controller_actions=("jobs:create",),
            certificate={
                "mode": "step_ca",
                "bootstrap": {
                    "mode": "step_ca_ott",
                    "ca_url": "https://step-ca.example.com",
                    "root_fingerprint": "0123abcd",
                    "ott": "ott_bootstrap_token",
                },
            },
        )
    )
    service = GatewayEnrollmentService(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_auth_mode="zitadel_service_account",
            upstream_certificate_mode="step_ca",
        ),
        identity_service=identity_service,
        secret_store=MemorySecretStore(),
        tls_context_factory=TlsContextFactory(AgentSettings()),
        certificate_service=certificate_service,
        auth_provider=StaticAuthProvider({"Authorization": "Bearer zitadel-token"}),
        metadata_path=tmp_path / "upstream-enrollment.json",
        snapshot_provider=_gateway_snapshot_payload,
        http_client_factory=lambda **kwargs: http_client,
    )

    record = await service.ensure_enrolled()

    assert record is not None
    assert record.data_plane.kind is UpstreamDataPlaneKind.ZENOH
    assert record.data_plane.namespace == "iot/v1/agents/agt_test"
    assert http_client.last_post_headers["Authorization"] == "Bearer zitadel-token"


@pytest.mark.anyio
async def test_enrollment_uses_bearer_enrollment_token_and_persists_step_ca_bootstrap_without_metadata_ott(
    tmp_path: Path,
) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    secret_store = MemorySecretStore()
    http_client = FakeAsyncHttpClient(
        response_payload=_enrollment_response_payload(
            controller_actions=("jobs:create",),
            certificate={
                "mode": "step_ca",
                "bootstrap": {
                    "mode": "step_ca_ott",
                    "ca_url": "https://step-ca.example.com",
                    "root_fingerprint": "0123abcd",
                    "ott": "ott_bootstrap_token",
                    "sign_url": "https://step-ca.example.com/1.0/sign",
                    "renew_url": "https://step-ca.example.com/1.0/renew",
                    "subject": "agt_test",
                    "authorized_sans": ["urn:inari:agt_test"],
                    "requires_mutual_tls_after_issuance": True,
                },
            },
        )
    )
    service = GatewayEnrollmentService(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_certificate_mode="step_ca",
            upstream_enrollment_token="bootstrap-token",
        ),
        identity_service=identity_service,
        secret_store=secret_store,
        tls_context_factory=TlsContextFactory(AgentSettings()),
        certificate_service=certificate_service,
        auth_provider=StaticAuthProvider({}),
        metadata_path=tmp_path / "upstream-enrollment.json",
        snapshot_provider=_gateway_snapshot_payload,
        http_client_factory=lambda **kwargs: http_client,
    )

    record = await service.ensure_enrolled()

    assert record is not None
    assert http_client.last_post_headers["Authorization"] == "Bearer bootstrap-token"
    assert "enrollment_code" not in http_client.last_post_json
    assert record.certificate_bootstrap is not None
    assert record.certificate_bootstrap.mode is CertificateBootstrapMode.STEP_CA_OTT
    assert record.certificate_bootstrap.ott == "ott_bootstrap_token"
    assert secret_store.get_secret(UPSTREAM_STEP_CA_OTT_KEY) == "ott_bootstrap_token"
    metadata = json.loads(
        (tmp_path / "upstream-enrollment.json").read_text(encoding="utf-8")
    )
    assert "certificate_bootstrap" in metadata
    assert "ott" not in metadata["certificate_bootstrap"]

    reloaded = service.load_enrollment()
    assert reloaded is not None
    assert reloaded.certificate_bootstrap is not None
    assert reloaded.certificate_bootstrap.ott == "ott_bootstrap_token"


@pytest.mark.anyio
async def test_step_ca_bootstrap_defaults_to_requiring_mtls_after_issuance(
    tmp_path: Path,
) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    service = GatewayEnrollmentService(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_certificate_mode="step_ca",
            upstream_enrollment_token="bootstrap-token",
        ),
        identity_service=identity_service,
        secret_store=MemorySecretStore(),
        tls_context_factory=TlsContextFactory(AgentSettings()),
        certificate_service=certificate_service,
        auth_provider=StaticAuthProvider({}),
        metadata_path=tmp_path / "upstream-enrollment.json",
        snapshot_provider=_gateway_snapshot_payload,
        http_client_factory=lambda **kwargs: FakeAsyncHttpClient(
            response_payload=_enrollment_response_payload(
                controller_actions=("jobs:create",),
                certificate={
                    "mode": "step_ca",
                    "bootstrap": {
                        "mode": "step_ca_ott",
                        "ca_url": "https://step-ca.example.com",
                        "root_fingerprint": "0123abcd",
                        "ott": "ott_bootstrap_token",
                    },
                },
            )
        ),
    )

    record = await service.ensure_enrolled()

    assert record is not None
    assert record.certificate_bootstrap is not None
    assert record.certificate_bootstrap.requires_mutual_tls_after_issuance is True


@pytest.mark.anyio
async def test_managed_certificate_lifecycle_bootstraps_issues_and_clears_ott(
    tmp_path: Path,
) -> None:
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
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    secret_store = MemorySecretStore()
    http_client = FakeAsyncHttpClient(
        response_payload=_enrollment_response_payload(
            controller_actions=("jobs:create",),
            certificate={
                "mode": "step_ca",
                "bootstrap": {
                    "mode": "step_ca_ott",
                    "ca_url": "https://step-ca.example.com",
                    "root_fingerprint": _fingerprint(ca_cert),
                    "ott": "ott_bootstrap_token",
                    "sign_url": "https://step-ca.example.com/1.0/sign",
                    "renew_url": "https://step-ca.example.com/1.0/renew",
                },
            },
        )
    )
    provisioner = StepCaOttCertificateProvisioner(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_certificate_mode="step_ca",
            upstream_enrollment_token="bootstrap-token",
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(ca_cert),
        ),
        identity_service=identity_service,
        certificate_service=certificate_service,
        http_client_factory=lambda **kwargs: StepCaHttpClient(
            root_pem=ca_pem,
            ca_key=ca_key,
            certificate_service=certificate_service,
        ),
    )
    enrollment_service = GatewayEnrollmentService(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_certificate_mode="step_ca",
            upstream_enrollment_token="bootstrap-token",
        ),
        identity_service=identity_service,
        secret_store=secret_store,
        tls_context_factory=TlsContextFactory(AgentSettings()),
        certificate_service=certificate_service,
        auth_provider=StaticAuthProvider({}),
        metadata_path=tmp_path / "upstream-enrollment.json",
        snapshot_provider=_gateway_snapshot_payload,
        http_client_factory=lambda **kwargs: http_client,
    )
    lifecycle = ManagedCertificateLifecycleManager(
        settings=AgentSettings(
            gateway_mode="managed",
            upstream_base_url="https://controller.example.com",
            upstream_certificate_mode="step_ca",
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(ca_cert),
        ),
        enrollment_service=enrollment_service,
        certificate_service=certificate_service,
        certificate_provisioner=provisioner,
    )

    await enrollment_service.ensure_enrolled()
    certificate = await lifecycle.ensure_current(trigger="test")

    assert certificate is not None
    assert lifecycle.current_status().state.value == "valid"
    reloaded = enrollment_service.load_enrollment()
    assert reloaded is not None
    assert reloaded.certificate_bootstrap is not None
    assert reloaded.certificate_bootstrap.ott is None


@pytest.mark.anyio
async def test_step_ca_ott_provisioner_bootstraps_root_and_issues_certificate(
    tmp_path: Path,
) -> None:
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
            authorized_sans=("urn:inari:agt_test",),
            requires_mutual_tls_after_issuance=True,
        )
    )

    certificate = await provisioner.ensure_certificate(enrollment)

    assert certificate is not None
    assert (tmp_path / "upstream-ca.pem").exists()
    assert (tmp_path / "upstream-client-cert.pem").exists()
    assert sign_client.sign_calls == 1


@pytest.mark.anyio
async def test_step_ca_ott_provisioner_replaces_rotated_root_certificate(
    tmp_path: Path,
) -> None:
    old_ca_key = ec.generate_private_key(ec.SECP256R1())
    old_ca_cert = _issue_certificate(
        subject_name="Old Step CA",
        issuer_name="Old Step CA",
        subject_key=old_ca_key.public_key(),
        issuer_key=old_ca_key,
        not_valid_after=datetime.now(tz=UTC) + timedelta(days=365),
        is_ca=True,
    )
    new_ca_key = ec.generate_private_key(ec.SECP256R1())
    new_ca_cert = _issue_certificate(
        subject_name="New Step CA",
        issuer_name="New Step CA",
        subject_key=new_ca_key.public_key(),
        issuer_key=new_ca_key,
        not_valid_after=datetime.now(tz=UTC) + timedelta(days=365),
        is_ca=True,
    )
    identity_path = tmp_path / "identity.pem"
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=identity_path,
        ca_path=tmp_path / "upstream-ca.pem",
    )
    certificate_service.install_certificate_authority(
        old_ca_cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    )
    provisioner = StepCaOttCertificateProvisioner(
        settings=AgentSettings(
            upstream_certificate_mode="step_ca",
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(new_ca_cert),
        ),
        identity_service=AgentIdentityService(identity_path=identity_path),
        certificate_service=certificate_service,
        http_client_factory=lambda **kwargs: StepCaHttpClient(
            root_pem=new_ca_cert.public_bytes(serialization.Encoding.PEM).decode(
                "utf-8"
            ),
            ca_key=new_ca_key,
            certificate_service=certificate_service,
        ),
    )

    certificate = await provisioner.ensure_certificate(
        _enrollment_record(
            certificate_bootstrap=StepCaOttBootstrap(
                mode=CertificateBootstrapMode.STEP_CA_OTT,
                ca_url="https://step-ca.example.com",
                root_fingerprint=_fingerprint(new_ca_cert),
                ott="ott_bootstrap_token",
                sign_url="https://step-ca.example.com/1.0/sign",
                renew_url="https://step-ca.example.com/1.0/renew",
            )
        )
    )

    assert certificate is not None
    installed_ca_pem = certificate_service.ca_path.read_text(encoding="utf-8")
    assert _fingerprint(
        x509.load_pem_x509_certificate(installed_ca_pem.encode("utf-8"))
    ) == _fingerprint(new_ca_cert)


def test_caddy_required_mtls_requires_enrollment_route_or_existing_certificate(
    tmp_path: Path,
) -> None:
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


def test_managed_gateway_requires_certificate_mode_for_zenoh() -> None:
    settings = AgentSettings(
        gateway_mode="managed",
        upstream_base_url="https://controller.example.com",
        upstream_certificate_mode="none",
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


def test_optional_mutual_tls_promotes_to_required_after_certificate_issuance() -> None:
    policy = resolve_mutual_tls_policy(
        MutualTlsMode.OPTIONAL,
        certificate_mode=UpstreamCertificateMode.STEP_CA,
        client_certificate_present=True,
        certificate_bootstrap=StepCaOttBootstrap(
            mode=CertificateBootstrapMode.STEP_CA_OTT,
            ca_url="https://ca.example.com",
            root_fingerprint="0123abcd",
        ),
    )

    assert policy.effective_mode is MutualTlsMode.REQUIRED
    assert policy.requires_client_certificate is True


def test_optional_mutual_tls_respects_explicit_post_issuance_opt_out() -> None:
    policy = resolve_mutual_tls_policy(
        MutualTlsMode.OPTIONAL,
        certificate_mode=UpstreamCertificateMode.STEP_CA,
        client_certificate_present=True,
        certificate_bootstrap=StepCaOttBootstrap(
            mode=CertificateBootstrapMode.STEP_CA_OTT,
            ca_url="https://ca.example.com",
            root_fingerprint="0123abcd",
            requires_mutual_tls_after_issuance=False,
        ),
    )

    assert policy.effective_mode is MutualTlsMode.OPTIONAL
    assert policy.requires_client_certificate is False


class StaticAuthProvider:
    mode = "static"

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers

    async def headers_for_enrollment(self) -> dict[str, str]:
        return dict(self.headers)

    async def invalidate(self) -> None:
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

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers=None,
    ):
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
                    "crt": certificate.public_bytes(serialization.Encoding.PEM).decode(
                        "utf-8"
                    ),
                    "ca": self.root_pem,
                }
            )
        certificate_path, _, _ = self.certificate_service.current_cert_chain()
        current_cert = (
            Path(certificate_path).read_text(encoding="utf-8")
            if certificate_path
            else self.root_pem
        )
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
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_name)])
        )
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
        .issuer_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example Step CA")])
        )
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
        enrolled_at=datetime.now(tz=UTC),
        data_plane=ZenohDataPlaneConfig(
            kind=UpstreamDataPlaneKind.ZENOH,
            session_mode=ZenohSessionMode.CLIENT,
            connect_endpoints=("tls/router.example.com:7447",),
            namespace="iot/v1/agents/agt_test",
            serialization=ZenohSerialization.JSON,
            auth_kind=ZenohDataPlaneAuthKind.MTLS,
            close_link_on_expiration=True,
        ),
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
            "name": "Inari",
            "version": API_VERSION,
            "agent_id": "agt_test",
            "key_id": "kid_test",
        },
        "security": {
            "mode": "managed",
            "exposure": "loopback",
            "tls_required": False,
            "edge_provider": "direct",
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
            "supported_controller_actions": ["jobs:create", "events:read"],
            "features": ["status_publication", "zenoh_data_plane"],
            "transport": "https+zenoh",
            "client_certificate_present": False,
        },
        "observability": {},
    }


def _enrollment_response_payload(
    *,
    controller_actions: tuple[str, ...] = (),
    certificate: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "selected_protocol_version": GATEWAY_PROTOCOL_VERSION,
        "controller": {
            "name": "Controller",
            "instance_id": "controller-1",
        },
        "permissions": {"controller_actions": list(controller_actions)},
        "data_plane": {
            "kind": "zenoh",
            "session_mode": "client",
            "connect_endpoints": ["tls/router.example.com:7447"],
            "namespace": "iot/v1/agents/agt_test",
            "serialization": "json",
            "auth": {"kind": "mtls"},
            "tls": {"close_link_on_expiration": True},
        },
        "certificate": certificate,
        "enrolled_at": "2026-04-13T00:00:00Z",
    }
