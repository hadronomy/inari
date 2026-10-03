from __future__ import annotations

from dataclasses import dataclass

from ...core.exceptions import AgentError
from ...gateway.models import CertificateEnrollmentSpec
from ..identity import AgentIdentityService


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
        subject = identity.agent_id
        requested_sans = (self.identity_service.default_uri_san(subject),)
        if enrollment.subject is not None and enrollment.subject != subject:
            raise AgentError(
                "STEP_CA_ENROLLMENT_SUBJECT_MISMATCH",
                "The certificate subject does not match this Agent Identity.",
                status_code=502,
            )
        if enrollment.authorized_sans and enrollment.authorized_sans != requested_sans:
            raise AgentError(
                "STEP_CA_ENROLLMENT_SAN_MISMATCH",
                "The certificate names do not match this Agent Identity.",
                status_code=502,
            )
        return ManagedCertificateRequest(
            csr_pem=self.identity_service.build_csr_pem(
                common_name=subject,
                uri_sans=requested_sans,
            ),
            subject=subject,
            requested_sans=requested_sans,
        )
