from __future__ import annotations

import asyncio
import json
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
from inari.core.exceptions import AgentError
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
    CertificateRenewalRequest,
    ClientCertificateProvider,
    ProvisionedCertificateMaterial,
    StepCaCertificateProvider,
    TrustBootstrapError,
    TrustBootstrapRequest,
    _verify_context,
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
                subject=identity_service.get_or_create_identity().agent_id,
                authorized_sans=(
                    identity_service.default_uri_san(
                        identity_service.get_or_create_identity().agent_id
                    ),
                ),
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
async def test_step_ca_provider_rejects_certificate_signed_by_wrong_ca(
    tmp_path: Path,
) -> None:
    trusted_ca_key = ec.generate_private_key(ec.SECP256R1())
    trusted_ca_cert = _issue_certificate(
        subject_name="Example Step CA",
        issuer_name="Example Step CA",
        subject_key=trusted_ca_key.public_key(),
        issuer_key=trusted_ca_key,
        not_valid_after=datetime.now(tz=UTC) + timedelta(days=365),
        is_ca=True,
    )
    rogue_ca_key = ec.generate_private_key(ec.SECP256R1())
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    certificate_service = CertificateLifecycleService(
        certificate_path=tmp_path / "upstream-client-cert.pem",
        private_key_path=tmp_path / "identity.pem",
        ca_path=tmp_path / "upstream-ca.pem",
    )
    certificate_service.install_certificate_authority(
        trusted_ca_cert.public_bytes(serialization.Encoding.PEM).decode()
    )
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            gateway_mode=GatewayMode.MANAGED,
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_url="https://step-ca.example.com",
        ),
        certificate_service=certificate_service,
        http_client_factory=_http_client_factory(
            StepCaHttpClient(
                root_pem=trusted_ca_cert.public_bytes(
                    serialization.Encoding.PEM
                ).decode("utf-8"),
                ca_key=rogue_ca_key,
                certificate_service=certificate_service,
            )
        ),
    )

    with pytest.raises(AgentError, match="does not chain"):
        await provider.enroll(
            CertificateEnrollmentRequest(
                enrollment=_certificate_enrollment(
                    root_fingerprint=_fingerprint(trusted_ca_cert),
                    token="ott_bootstrap_token",
                    subject=identity_service.get_or_create_identity().agent_id,
                ),
                csr_pem=identity_service.build_csr_pem(),
            )
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case", ["valid", "rogue_root", "not_ca", "expired", "missing_intermediate"]
)
async def test_step_ca_chain_ends_at_the_pinned_root(tmp_path: Path, case: str) -> None:
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = _issue_certificate(
        subject_name="Pinned root",
        issuer_name="Pinned root",
        subject_key=root_key.public_key(),
        issuer_key=root_key,
        not_valid_after=datetime.now(UTC) + timedelta(days=365),
        is_ca=True,
    )
    root_pem = root.public_bytes(serialization.Encoding.PEM).decode()
    issuer_key = ec.generate_private_key(ec.SECP256R1())
    issuer = _issue_certificate(
        subject_name="Intermediate",
        issuer_name="Pinned root",
        subject_key=issuer_key.public_key(),
        issuer_key=ec.generate_private_key(ec.SECP256R1())
        if case == "rogue_root"
        else root_key,
        not_valid_after=datetime.now(UTC)
        + (timedelta(seconds=-10) if case == "expired" else timedelta(days=30)),
        is_ca=case != "not_ca",
    )
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    csr_pem = identity.build_csr_pem()
    certificate = _issue_certificate_from_csr(
        x509.load_pem_x509_csr(csr_pem.encode()), issuer_key, issuer_name="Intermediate"
    )
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=identity.identity_path,
        ca_path=tmp_path / "ca.pem",
    )
    service.install_certificate_authority(root_pem)
    response = {
        "crt": certificate.public_bytes(serialization.Encoding.PEM).decode(),
        "ca": ""
        if case == "missing_intermediate"
        else issuer.public_bytes(serialization.Encoding.PEM).decode(),
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA
        ),
        certificate_service=service,
        http_client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=transport, **kwargs
        ),
    )
    request = CertificateEnrollmentRequest(
        enrollment=_certificate_enrollment(
            token="test-ott", root_fingerprint=_fingerprint(root)
        ),
        csr_pem=csr_pem,
    )
    if case == "valid":
        material = await provider.enroll(request)
        assert material is not None
        chain = x509.load_pem_x509_certificates(material.certificate_chain_pem.encode())
        assert chain == [certificate, issuer]
        service.install(certificate_pem=material.certificate_chain_pem)
        assert (
            x509.load_pem_x509_certificates(service.certificate_path.read_bytes())
            == chain
        )
    else:
        with pytest.raises(AgentError, match="pinned CA root"):
            await provider.enroll(request)
    assert service.ca_path is not None
    assert service.ca_path.read_text() == root_pem


@pytest.mark.anyio
@pytest.mark.parametrize("bundled_response", [False, True])
async def test_root_bootstrap_rejects_extra_trust_anchors(
    tmp_path: Path, bundled_response: bool
) -> None:
    roots = []
    for name in ("Pinned root", "Rogue root"):
        key = ec.generate_private_key(ec.SECP256R1())
        roots.append(
            _issue_certificate(
                subject_name=name,
                issuer_name=name,
                subject_key=key.public_key(),
                issuer_key=key,
                not_valid_after=datetime.now(UTC) + timedelta(days=365),
                is_ca=True,
            )
        )
    pinned_pem, rogue_pem = (
        root.public_bytes(serialization.Encoding.PEM).decode() for root in roots
    )
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=tmp_path / "key.pem",
        ca_path=tmp_path / "root.pem",
    )
    service.install_certificate_authority(pinned_pem + rogue_pem)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=pinned_pem + (rogue_pem if bundled_response else "")
        )
    )
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA
        ),
        certificate_service=service,
        http_client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=transport, **kwargs
        ),
    )
    request = TrustBootstrapRequest(
        enrollment=_certificate_enrollment(root_fingerprint=_fingerprint(roots[0]))
    )
    if bundled_response:
        with pytest.raises(TrustBootstrapError, match="exactly one CA"):
            await provider.bootstrap_trust(request)
        with pytest.raises(TrustBootstrapError, match="exactly one CA"):
            _verify_context(provider._pinned_root(request.enrollment))
    else:
        await provider.bootstrap_trust(request)
        assert service.ca_path is not None
        assert service.ca_path.read_text() == pinned_pem
        context = _verify_context(provider._pinned_root(request.enrollment))
        assert context.check_hostname
        assert context.get_ca_certs(binary_form=True) == [
            roots[0].public_bytes(serialization.Encoding.DER)
        ]


@pytest.mark.anyio
async def test_missing_root_pin_or_root_stops_before_the_http_request(
    tmp_path: Path,
) -> None:
    calls = 0

    def unexpected_client(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("certificate request must not start without pinned trust")

    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA
        ),
        certificate_service=CertificateLifecycleService(
            certificate_path=tmp_path / "client.pem",
            private_key_path=tmp_path / "key.pem",
            ca_path=tmp_path / "root.pem",
        ),
        http_client_factory=unexpected_client,
    )
    for pin in ("", "invalid"):
        with pytest.raises(TrustBootstrapError):
            await provider.bootstrap_trust(
                TrustBootstrapRequest(
                    enrollment=_certificate_enrollment(root_fingerprint=pin)
                )
            )
    with pytest.raises(TrustBootstrapError, match="No pinned step-ca root"):
        await provider.enroll(
            CertificateEnrollmentRequest(
                enrollment=_certificate_enrollment(
                    token="test-ott", root_fingerprint="a" * 64
                ),
                csr_pem="unused",
            )
        )
    assert calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("renew", [False, True])
async def test_client_certificate_must_permit_tls_signatures(
    tmp_path: Path, renew: bool
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    root = _issue_certificate(
        subject_name="Example Step CA",
        issuer_name="Example Step CA",
        subject_key=key.public_key(),
        issuer_key=key,
        not_valid_after=datetime.now(UTC) + timedelta(days=365),
        is_ca=True,
    )
    root_pem = root.public_bytes(serialization.Encoding.PEM).decode()
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    csr_pem = identity.build_csr_pem()
    certificate = _issue_certificate_from_csr(
        x509.load_pem_x509_csr(csr_pem.encode()), key, digital_signature=False
    )
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=identity.identity_path,
        ca_path=tmp_path / "ca.pem",
    )
    service.install_certificate_authority(root_pem)
    service.install(
        certificate_pem=_issue_certificate_from_csr(
            x509.load_pem_x509_csr(csr_pem.encode()), key
        )
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    before = service.certificate_path.read_bytes()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "crt": certificate.public_bytes(serialization.Encoding.PEM).decode(),
                "ca": root_pem,
            },
        )
    )
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA
        ),
        certificate_service=service,
        http_client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=transport, **kwargs
        ),
    )
    with pytest.raises(AgentError, match="cannot sign a TLS handshake"):
        if renew:
            await provider.renew(
                CertificateRenewalRequest(
                    enrollment=_certificate_enrollment(
                        root_fingerprint=_fingerprint(root)
                    )
                )
            )
        else:
            await provider.enroll(
                CertificateEnrollmentRequest(
                    enrollment=_certificate_enrollment(
                        token="test-ott", root_fingerprint=_fingerprint(root)
                    ),
                    csr_pem=csr_pem,
                )
            )
    assert service.certificate_path.read_bytes() == before


@pytest.mark.anyio
@pytest.mark.parametrize("renew", [False, True])
@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "subject",
        "extra_subject",
        "missing_san",
        "other_uri",
        "dns",
        "extra_san",
        "duplicate_san",
        "other_name",
    ],
)
async def test_step_ca_validates_identity_before_returning_material(
    tmp_path: Path, renew: bool, mutation: str
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
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode()
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    csr_pem = identity.build_csr_pem()
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=identity.identity_path,
        ca_path=tmp_path / "ca.pem",
    )
    current = _issue_certificate_from_csr(csr, ca_key)
    current_pem = current.public_bytes(serialization.Encoding.PEM).decode()
    if renew:
        service.install(certificate_pem=current_pem, ca_certificate_pem=ca_pem)
    else:
        service.install_certificate_authority(ca_pem)

    expected_uri = identity.default_uri_san(identity.get_or_create_identity().agent_id)
    subject = csr.subject
    sans: list[x509.GeneralName] = [x509.UniformResourceIdentifier(expected_uri)]
    if mutation == "subject":
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agt_other")])
    elif mutation == "extra_subject":
        subject = x509.Name(
            [*subject, x509.NameAttribute(NameOID.COMMON_NAME, "agt_other")]
        )
    elif mutation == "other_uri":
        sans = [x509.UniformResourceIdentifier("urn:inari:agt_other")]
    elif mutation == "dns":
        sans = [x509.DNSName(expected_uri)]
    elif mutation == "extra_san":
        sans.append(x509.DNSName("other.example.com"))
    elif mutation == "duplicate_san":
        sans += sans
    elif mutation == "other_name":
        sans.append(x509.OtherName(x509.ObjectIdentifier("1.2.3.4"), b"\x0c\x05other"))
    builder = x509.CertificateSigningRequestBuilder().subject_name(subject)
    if mutation != "missing_san":
        builder = builder.add_extension(
            x509.SubjectAlternativeName(sans), critical=False
        )
    key = serialization.load_pem_private_key(identity.identity_path.read_bytes(), None)
    returned = _issue_certificate_from_csr(builder.sign(key, None), ca_key)
    response = {
        "crt": returned.public_bytes(serialization.Encoding.PEM).decode(),
        "ca": ca_pem,
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))

    def client_factory(**kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA
        ),
        certificate_service=service,
        http_client_factory=client_factory,
    )
    enrollment = _certificate_enrollment(
        root_fingerprint=_fingerprint(ca_cert),
        subject=identity.get_or_create_identity().agent_id,
        authorized_sans=(expected_uri,),
        token="test-one-time-token",
    )
    operation = (
        provider.renew(CertificateRenewalRequest(enrollment=enrollment))
        if renew
        else provider.enroll(
            CertificateEnrollmentRequest(enrollment=enrollment, csr_pem=csr_pem)
        )
    )
    if mutation == "valid":
        assert await operation is not None
    else:
        with pytest.raises(AgentError, match="Agent Identity|alternative name"):
            await operation
    if renew:
        assert service.certificate_path.read_text() == current_pem
    else:
        assert not service.certificate_path.exists()


def test_certificate_request_preserves_enrollment_csr_fingerprint(
    tmp_path: Path,
) -> None:
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    csr = identity.build_csr_pem()
    agent_id = identity.get_or_create_identity().agent_id
    crypto = ManagedCertificateCryptoService(identity_service=identity)
    request = crypto.build_request(
        _certificate_enrollment(
            subject=agent_id, authorized_sans=(identity.default_uri_san(agent_id),)
        )
    )
    assert request.csr_pem == csr
    for enrollment in [
        _certificate_enrollment(subject="agt_other"),
        _certificate_enrollment(authorized_sans=("urn:inari:agt_other",)),
    ]:
        with pytest.raises(AgentError, match="Agent Identity"):
            crypto.build_request(enrollment)


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

    def validate_current(self, request) -> None:
        del request

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
        return ProvisionedCertificateMaterial(certificate_chain_pem=certificate)


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
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(subject_key), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
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
    issuer_name: str = "Example Step CA",
    digital_signature: bool = True,
):
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(minutes=1))
        .not_valid_after(not_valid_after or (datetime.now(tz=UTC) + timedelta(days=7)))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=digital_signature,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("installed", "fresh_bootstrap"),
    [
        ("intermediate", False),
        ("rogue_root", False),
        ("old_san", False),
        ("old_san", True),
    ],
)
async def test_lifecycle_checks_installed_trust_and_identity_before_use(
    tmp_path: Path, installed: str, fresh_bootstrap: bool
) -> None:
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = _issue_certificate(
        subject_name="Example Step CA",
        issuer_name="Example Step CA",
        subject_key=root_key.public_key(),
        issuer_key=root_key,
        not_valid_after=datetime.now(UTC) + timedelta(days=365),
        is_ca=True,
    )
    root_pem = root.public_bytes(serialization.Encoding.PEM).decode()
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    csr_pem = identity.build_csr_pem()
    identity_bytes = identity.identity_path.read_bytes()
    issuer_key = root_key
    issuer_name = "Example Step CA"
    cached_ca = root
    intermediate_pem = ""
    if installed in {"intermediate", "rogue_root"}:
        issuer_key = ec.generate_private_key(ec.SECP256R1())
        issuer_name = "Intermediate" if installed == "intermediate" else "Rogue root"
        cached_ca = _issue_certificate(
            subject_name=issuer_name,
            issuer_name="Example Step CA"
            if installed == "intermediate"
            else issuer_name,
            subject_key=issuer_key.public_key(),
            issuer_key=root_key if installed == "intermediate" else issuer_key,
            not_valid_after=datetime.now(UTC) + timedelta(days=30),
            is_ca=True,
        )
        intermediate_pem = cached_ca.public_bytes(serialization.Encoding.PEM).decode()
    old_csr = (
        identity.build_csr_pem(uri_sans=("urn:old:shared",))
        if installed == "old_san"
        else csr_pem
    )
    leaf = _issue_certificate_from_csr(
        x509.load_pem_x509_csr(old_csr.encode()), issuer_key, issuer_name=issuer_name
    )
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=identity.identity_path,
        ca_path=tmp_path / "ca.pem",
    )
    service.install(
        certificate_pem=leaf.public_bytes(serialization.Encoding.PEM).decode()
        + intermediate_pem,
        ca_certificate_pem=cached_ca.public_bytes(serialization.Encoding.PEM).decode(),
    )
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.url.path.endswith(_fingerprint(root))
            return httpx.Response(200, text=root_pem)
        assert fresh_bootstrap and installed == "old_san"
        assert request.url.path.endswith("/sign")

        signed_csr = x509.load_pem_x509_csr(json.loads(request.content)["csr"].encode())
        replacement = _issue_certificate_from_csr(signed_csr, root_key)
        return httpx.Response(
            200,
            json={"crt": replacement.public_bytes(serialization.Encoding.PEM).decode()},
        )

    settings = AgentSettings(upstream_certificate_mode=UpstreamCertificateMode.STEP_CA)
    provider = StepCaCertificateProvider(
        settings=settings,
        certificate_service=service,
        http_client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    enrollment = StubEnrollmentService(
        _enrollment_record(
            certificate_enrollment=_certificate_enrollment(
                root_fingerprint=_fingerprint(root),
                token="fresh-ott" if fresh_bootstrap else None,
                subject=identity.get_or_create_identity().agent_id,
                authorized_sans=(
                    identity.default_uri_san(
                        identity.get_or_create_identity().agent_id
                    ),
                ),
            )
        )
    )
    lifecycle = ManagedCertificateLifecycleManager(
        settings=settings,
        enrollment_service=cast(GatewayEnrollmentService, enrollment),
        certificate_service=service,
        certificate_provider=provider,
        certificate_crypto_service=ManagedCertificateCryptoService(
            identity_service=identity
        ),
    )
    current = await lifecycle.ensure_current()
    assert service.ca_path is not None and service.ca_path.read_text() == root_pem
    assert identity.identity_path.read_bytes() == identity_bytes
    if installed == "intermediate" or fresh_bootstrap:
        assert current is not None
        assert lifecycle.current_status().state is ManagedCertificateState.VALID
        provider.validate_current(
            CertificateRenewalRequest(
                enrollment=enrollment.record.certificate_enrollment
            )
        )
        assert sum(request.method == "POST" for request in requests) == int(
            fresh_bootstrap
        )
    else:
        assert current is None
        assert (
            lifecycle.current_status().state
            is ManagedCertificateState.REBOOTSTRAP_REQUIRED
        )
        assert all(request.method == "GET" for request in requests)


@pytest.mark.anyio
@pytest.mark.parametrize("fingerprint", [None, "a" * 64])
async def test_renewal_rejects_unpinned_cached_ca_before_http(
    tmp_path: Path, fingerprint: str | None
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    root = _issue_certificate(
        subject_name="Example Step CA",
        issuer_name="Example Step CA",
        subject_key=key.public_key(),
        issuer_key=key,
        not_valid_after=datetime.now(UTC) + timedelta(days=365),
        is_ca=True,
    )
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    leaf = _issue_certificate_from_csr(
        x509.load_pem_x509_csr(identity.build_csr_pem().encode()), key
    )
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=identity.identity_path,
        ca_path=tmp_path / "ca.pem",
    )
    service.install(
        certificate_pem=leaf.public_bytes(serialization.Encoding.PEM).decode(),
        ca_certificate_pem=root.public_bytes(serialization.Encoding.PEM).decode(),
    )

    def unexpected_http(**kwargs):
        raise AssertionError("unverified trust must not reach the CA")

    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_renew_url="https://ca.example.com/renew",
            step_ca_root_fingerprint=fingerprint,
        ),
        certificate_service=service,
        http_client_factory=unexpected_http,
    )
    with pytest.raises(TrustBootstrapError):
        await provider.renew(CertificateRenewalRequest())


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["root", "sign", "renew"])
@pytest.mark.parametrize(
    "url",
    [
        "http://ca.example.com",
        "https://user:pass@ca.example.com",
        "https://ca.example.com?token=x",
        "https://ca.example.com#fragment",
    ],
)
async def test_ca_endpoints_reject_unsafe_urls_before_http(
    tmp_path: Path, operation: str, url: str
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    root = _issue_certificate(
        subject_name="Example Step CA",
        issuer_name="Example Step CA",
        subject_key=key.public_key(),
        issuer_key=key,
        not_valid_after=datetime.now(UTC) + timedelta(days=365),
        is_ca=True,
    )
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=tmp_path / "key.pem",
        ca_path=tmp_path / "ca.pem",
    )
    identity = AgentIdentityService(identity_path=service.private_key_path)
    csr_pem = identity.build_csr_pem()
    service.install(
        certificate_pem=_issue_certificate_from_csr(
            x509.load_pem_x509_csr(csr_pem.encode()), key
        )
        .public_bytes(serialization.Encoding.PEM)
        .decode(),
        ca_certificate_pem=root.public_bytes(serialization.Encoding.PEM).decode(),
    )

    def unexpected_http(**kwargs):
        raise AssertionError("unsafe URL must not receive credentials")

    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA,
            step_ca_sign_url=url if operation == "sign" else None,
            step_ca_renew_url=url if operation == "renew" else None,
        ),
        certificate_service=service,
        http_client_factory=unexpected_http,
    )
    enrollment = replace(
        _certificate_enrollment(root_fingerprint=_fingerprint(root), token="ott"),
        base_url=url if operation == "root" else "https://ca.example.com",
    )
    with pytest.raises(TrustBootstrapError, match="requires HTTPS"):
        if operation == "root":
            await provider.bootstrap_trust(TrustBootstrapRequest(enrollment=enrollment))
        elif operation == "sign":
            await provider.enroll(
                CertificateEnrollmentRequest(enrollment=enrollment, csr_pem=csr_pem)
            )
        else:
            await provider.renew(CertificateRenewalRequest(enrollment=enrollment))


@pytest.mark.anyio
@pytest.mark.parametrize("renew", [False, True])
async def test_step_ca_preserves_all_intermediates_when_ca_and_chain_are_present(
    tmp_path: Path, renew: bool
) -> None:
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = _issue_certificate(
        subject_name="Root",
        issuer_name="Root",
        subject_key=root_key.public_key(),
        issuer_key=root_key,
        not_valid_after=datetime.now(UTC) + timedelta(days=365),
        is_ca=True,
    )
    upper_key = ec.generate_private_key(ec.SECP256R1())
    upper = _issue_certificate(
        subject_name="Upper CA",
        issuer_name="Root",
        subject_key=upper_key.public_key(),
        issuer_key=root_key,
        not_valid_after=datetime.now(UTC) + timedelta(days=30),
        is_ca=True,
    )
    lower_key = ec.generate_private_key(ec.SECP256R1())
    lower = _issue_certificate(
        subject_name="Lower CA",
        issuer_name="Upper CA",
        subject_key=lower_key.public_key(),
        issuer_key=upper_key,
        not_valid_after=datetime.now(UTC) + timedelta(days=7),
        is_ca=True,
    )
    identity = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    csr_pem = identity.build_csr_pem()
    leaf = _issue_certificate_from_csr(
        x509.load_pem_x509_csr(csr_pem.encode()), lower_key, issuer_name="Lower CA"
    )
    chain = [
        cert.public_bytes(serialization.Encoding.PEM).decode()
        for cert in (leaf, lower, upper)
    ]
    service = CertificateLifecycleService(
        certificate_path=tmp_path / "client.pem",
        private_key_path=identity.identity_path,
        ca_path=tmp_path / "ca.pem",
    )
    service.install_certificate_authority(
        root.public_bytes(serialization.Encoding.PEM).decode()
    )
    if renew:
        service.install(certificate_pem="".join(chain))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"crt": chain[0], "ca": chain[1], "certChain": chain}
        )
    )
    provider = StepCaCertificateProvider(
        settings=AgentSettings(
            upstream_certificate_mode=UpstreamCertificateMode.STEP_CA
        ),
        certificate_service=service,
        http_client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=transport, **kwargs
        ),
    )
    enrollment = _certificate_enrollment(
        root_fingerprint=_fingerprint(root), token="ott"
    )
    material = (
        await provider.renew(CertificateRenewalRequest(enrollment=enrollment))
        if renew
        else await provider.enroll(
            CertificateEnrollmentRequest(enrollment=enrollment, csr_pem=csr_pem)
        )
    )
    assert material is not None
    assert x509.load_pem_x509_certificates(material.certificate_chain_pem.encode()) == [
        leaf,
        lower,
        upper,
    ]
