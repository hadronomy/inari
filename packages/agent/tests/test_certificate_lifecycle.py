from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from tests.factories import (
    certificate_enrollment as _certificate_enrollment,
    enrollment_record as _enrollment_record,
)
from inari.config import AgentSettings
from inari.gateway.enrollment import GatewayEnrollmentService
from inari.gateway.models import (
    GatewayEnrollmentRecord,
    ManagedCertificateFailureReason,
    ManagedCertificateState,
    UpstreamCertificateMode,
)
from inari.security.certificates.crypto import ManagedCertificateCryptoService
from inari.security.certificates.lifecycle import ManagedCertificateLifecycleManager
from inari.security.certificates.providers import (
    CertificateEnrollmentRequest,
    ClientCertificateProvider,
    ProvisionedCertificateMaterial,
    StepCaCertificateProvider,
)
from inari.security.certificates import CertificateLifecycleService, ManagedCertificate
from inari.security.identity import AgentIdentityService
from inari.security.models import GatewayMode


@pytest.mark.anyio
async def test_lifecycle_waits_for_bootstrap_when_certificate_is_missing(
    tmp_path: Path,
) -> None:
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    lifecycle = ManagedCertificateLifecycleManager(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
        ),
        enrollment_service=cast(
            GatewayEnrollmentService,
            StubEnrollmentService(
                _enrollment_record(certificate_enrollment=_certificate_enrollment())
            ),
        ),
        certificate_service=certificate_service,
        certificate_provider=cast(
            ClientCertificateProvider,
            PendingProvider(),
        ),
        certificate_crypto_service=ManagedCertificateCryptoService(
            identity_service=identity_service
        ),
    )

    certificate = await lifecycle.ensure_current(trigger="test")

    assert certificate is None
    status = lifecycle.current_status()
    assert status.state is ManagedCertificateState.WAITING_FOR_BOOTSTRAP
    assert status.failure_reason is ManagedCertificateFailureReason.BOOTSTRAP_REQUIRED


@pytest.mark.anyio
async def test_lifecycle_recovers_invalid_local_certificate_with_fresh_bootstrap(
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
    certificate_service.certificate_path.write_text(
        "not-a-certificate", encoding="utf-8"
    )
    enrollment_service = StubEnrollmentService(
        _enrollment_record(
            certificate_enrollment=_certificate_enrollment(
                root_fingerprint=_fingerprint(ca_cert),
                token="ott_bootstrap_token",
                subject="agt_test",
                authorized_sans=("urn:inari:agt_test",),
            )
        )
    )
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(ca_cert),
        ),
        certificate_service=certificate_service,
        http_client_factory=_http_client_factory(
            StepCaHttpClient(
                root_pem=ca_pem,
                ca_key=ca_key,
                certificate_service=certificate_service,
            )
        ),
    )
    lifecycle = ManagedCertificateLifecycleManager(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(ca_cert),
        ),
        enrollment_service=cast(GatewayEnrollmentService, enrollment_service),
        certificate_service=certificate_service,
        certificate_provider=provider,
        certificate_crypto_service=ManagedCertificateCryptoService(
            identity_service=identity_service
        ),
    )

    certificate = await lifecycle.ensure_current(trigger="test")

    assert certificate is not None
    assert lifecycle.current_status().state is ManagedCertificateState.VALID
    assert "BEGIN CERTIFICATE" in certificate_service.certificate_path.read_text(
        encoding="utf-8"
    )
    assert enrollment_service.record is not None
    assert enrollment_service.record.certificate_enrollment is not None
    assert enrollment_service.record.certificate_enrollment.bootstrap_auth is not None
    assert enrollment_service.record.certificate_enrollment.bootstrap_auth.token is None


@pytest.mark.anyio
async def test_lifecycle_marks_rebootstrap_required_after_renewal_rejection(
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
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    current_cert = _issue_certificate_from_csr(
        x509.load_pem_x509_csr(identity_service.build_csr_pem().encode("utf-8")),
        ca_key,
        not_valid_after=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    certificate_service.install(
        certificate_pem=current_cert.public_bytes(serialization.Encoding.PEM).decode(
            "utf-8"
        ),
        ca_certificate_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode(
            "utf-8"
        ),
    )
    enrollment_service = StubEnrollmentService(
        _enrollment_record(
            certificate_enrollment=_certificate_enrollment(
                root_fingerprint=_fingerprint(ca_cert)
            )
        )
    )
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_url="https://step-ca.example.com",
            step_ca_root_fingerprint=_fingerprint(ca_cert),
            step_ca_certificate_renewal_skew_seconds=3600,
        ),
        certificate_service=certificate_service,
        http_client_factory=_http_client_factory(RejectingRenewHttpClient()),
    )
    lifecycle = ManagedCertificateLifecycleManager(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_certificate_renewal_skew_seconds=3600,
        ),
        enrollment_service=cast(GatewayEnrollmentService, enrollment_service),
        certificate_service=certificate_service,
        certificate_provider=provider,
        certificate_crypto_service=ManagedCertificateCryptoService(
            identity_service=identity_service
        ),
    )

    certificate = await lifecycle.ensure_current(trigger="test")

    assert certificate is not None
    status = lifecycle.current_status()
    assert status.state is ManagedCertificateState.REBOOTSTRAP_REQUIRED
    assert status.failure_reason is ManagedCertificateFailureReason.AUTH_FAILED


@pytest.mark.anyio
async def test_lifecycle_serializes_concurrent_issuance_attempts(
    tmp_path: Path,
) -> None:
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    enrollment_service = StubEnrollmentService(
        _enrollment_record(
            certificate_enrollment=_certificate_enrollment(token="ott_bootstrap_token")
        )
    )
    provider = SlowProvider()
    lifecycle = ManagedCertificateLifecycleManager(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
        ),
        enrollment_service=cast(GatewayEnrollmentService, enrollment_service),
        certificate_service=certificate_service,
        certificate_provider=cast(
            ClientCertificateProvider,
            provider,
        ),
        certificate_crypto_service=ManagedCertificateCryptoService(
            identity_service=identity_service
        ),
    )

    results = await asyncio.gather(
        lifecycle.ensure_current(trigger="test"),
        lifecycle.ensure_current(trigger="test"),
        lifecycle.ensure_current(trigger="test"),
    )

    assert provider.calls == 1
    assert all(result is not None for result in results)
    assert lifecycle.current_status().successful_issue_count == 1


class StubEnrollmentService:
    def __init__(self, record: GatewayEnrollmentRecord | None) -> None:
        self.record = record

    def load_enrollment(self) -> GatewayEnrollmentRecord | None:
        return self.record

    def persist_certificate_state(
        self,
        record: GatewayEnrollmentRecord,
        *,
        certificate: ManagedCertificate | None,
        clear_bootstrap_auth: bool,
    ) -> GatewayEnrollmentRecord:
        self.record = replace(
            record,
            certificate_expires_at=certificate.not_valid_after
            if certificate is not None
            else None,
        )
        if clear_bootstrap_auth and certificate is not None:
            self.record = self.record.clear_bootstrap_token()
        return self.record


class PendingProvider:
    manages_client_certificate = True

    async def bootstrap_trust(self, request) -> None:
        del request
        return None

    async def enroll(
        self, request: CertificateEnrollmentRequest
    ) -> ProvisionedCertificateMaterial | None:
        del request
        return None

    async def renew(self, request) -> ProvisionedCertificateMaterial | None:
        del request
        return None


class SlowProvider(PendingProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def enroll(
        self, request: CertificateEnrollmentRequest
    ) -> ProvisionedCertificateMaterial | None:
        del request
        self.calls += 1
        await asyncio.sleep(0.05)
        certificate = _issue_ephemeral_certificate("agt_test")
        return ProvisionedCertificateMaterial(leaf_certificate_pem=certificate)


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

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str):
        return FakeTextResponse(self.root_pem)

    async def post(self, url: str, *, json=None, headers=None):
        if url.endswith("/sign"):
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


class RejectingRenewHttpClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json=None, headers=None):
        return FakeAsyncResponse({}, status_code=401)


class FakeAsyncResponse:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.text = "{}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://step-ca.example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

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
    if not is_ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def _issue_certificate_from_csr(
    csr: x509.CertificateSigningRequest,
    issuer_key,
    *,
    not_valid_after: datetime | None = None,
):
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example Step CA")])
        )
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(minutes=1))
        .not_valid_after(not_valid_after or (datetime.now(tz=UTC) + timedelta(days=7)))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    )
    for extension in csr.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    return builder.sign(issuer_key, hashes.SHA256())


def _issue_ephemeral_certificate(subject_name: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = _issue_certificate(
        subject_name=subject_name,
        issuer_name=subject_name,
        subject_key=key.public_key(),
        issuer_key=key,
        not_valid_after=datetime.now(tz=UTC) + timedelta(days=7),
        is_ca=False,
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8")


def _fingerprint(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex()


def _http_client_factory(client: object) -> Callable[..., httpx.AsyncClient]:
    return cast(Callable[..., httpx.AsyncClient], lambda **kwargs: client)
