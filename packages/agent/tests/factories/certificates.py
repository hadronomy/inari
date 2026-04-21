from __future__ import annotations

from datetime import UTC, datetime

from inari.gateway.models import (
    CertificateBootstrapAuth,
    CertificateBootstrapAuthType,
    CertificateEnrollmentSpec,
    CertificateTrustSpec,
    GatewayEnrollmentRecord,
    UpstreamDataPlaneKind,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
)


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
    protocol_version: str | None = None,
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
