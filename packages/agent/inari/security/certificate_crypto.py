from __future__ import annotations

from dataclasses import dataclass

from ..gateway.models import CertificateEnrollmentSpec
from .identity import AgentIdentityService


@dataclass(slots=True, frozen=True, kw_only=True)
class ManagedCertificateRequest:
    csr_pem: str
    subject: str
    requested_sans: tuple[str, ...]


class ManagedCertificateCryptoService:
    def __init__(self, *, identity_service: AgentIdentityService) -> None:
        self.identity_service = identity_service

    def build_request(
        self,
        enrollment: CertificateEnrollmentSpec,
    ) -> ManagedCertificateRequest:
        identity = self.identity_service.get_or_create_identity()
        subject = enrollment.subject or identity.agent_id
        requested_sans = enrollment.authorized_sans or (
            self.identity_service.default_uri_san(identity.agent_id),
        )
        return ManagedCertificateRequest(
            csr_pem=self.identity_service.build_csr_pem(
                common_name=subject,
                uri_sans=requested_sans,
            ),
            subject=subject,
            requested_sans=requested_sans,
        )
