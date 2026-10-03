from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from inari.core.version import GATEWAY_PROTOCOL_VERSION
from inari.gateway.models import (
    CertificateBootstrapAuth,
    CertificateBootstrapAuthType,
    CertificateEnrollmentSpec,
    CertificateTrustSpec,
    GatewayEnrollmentRecord,
    ManagedCertificateState,
    ManagedCertificateStatus,
    UpstreamDataPlaneKind,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
)
from inari.security.certificates.store import ManagedCertificate


def certificate_enrollment(
    *,
    base_url: str = "https://step-ca.example.com",
    root_fingerprint: str = "fingerprint",
    token: str | None = None,
    subject: str | None = None,
    authorized_sans: tuple[str, ...] = (),
    requires_mutual_tls_after_issuance: bool = True,
) -> CertificateEnrollmentSpec:
    return CertificateEnrollmentSpec(
        base_url=base_url,
        trust=CertificateTrustSpec(root_fingerprint=root_fingerprint),
        bootstrap_auth=(
            CertificateBootstrapAuth(
                type=CertificateBootstrapAuthType.OTT,
                token=token,
            )
            if token is not None
            else None
        ),
        subject=subject,
        authorized_sans=authorized_sans,
        requires_mutual_tls_after_issuance=requires_mutual_tls_after_issuance,
    )


def enrollment_record(
    *,
    certificate_enrollment: CertificateEnrollmentSpec | None = None,
    protocol_version: str = GATEWAY_PROTOCOL_VERSION,
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
        protocol_version=protocol_version,
        certificate_enrollment=certificate_enrollment,
    )


class StaticCertificateLifecycle:
    """Certificate lifecycle stand-in with a fixed admission outcome."""

    def __init__(
        self,
        certificate: ManagedCertificate | None,
        *,
        status: ManagedCertificateStatus | None = None,
    ) -> None:
        self.certificate = certificate
        self.status = status or ManagedCertificateStatus(
            state=(
                ManagedCertificateState.VALID
                if certificate is not None
                else ManagedCertificateState.REBOOTSTRAP_REQUIRED
            ),
            detail=(
                "Managed client certificate is healthy."
                if certificate is not None
                else "Fresh enrollment is required."
            ),
            certificate_present=certificate is not None,
        )
        self.triggers: list[str] = []

    async def ensure_current(
        self,
        *,
        enrollment: GatewayEnrollmentRecord | None = None,
        trigger: str = "manual",
    ) -> ManagedCertificate | None:
        del enrollment
        self.triggers.append(trigger)
        return self.certificate

    def current_status(self) -> ManagedCertificateStatus:
        return self.status


def managed_certificate(path: Path) -> ManagedCertificate:
    return ManagedCertificate(
        certificate_path=path,
        ca_path=None,
        not_valid_after=datetime.now(tz=UTC) + timedelta(days=1),
        subject="CN=agt_test",
        issuer="CN=Example Step CA",
        serial_number="1",
    )
