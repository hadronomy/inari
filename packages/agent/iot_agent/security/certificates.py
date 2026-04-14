from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509


@dataclass(slots=True, frozen=True)
class ManagedCertificate:
    certificate_path: Path
    ca_path: Path | None
    not_valid_after: datetime | None
    subject: str | None
    issuer: str | None
    serial_number: str | None


@dataclass(slots=True, frozen=True)
class ManagedCertificateInspection:
    certificate: ManagedCertificate | None
    error_detail: str | None = None


class CertificateLifecycleService:
    def __init__(
        self,
        *,
        certificate_path: Path,
        private_key_path: Path,
        ca_path: Path | None = None,
    ) -> None:
        self.certificate_path = certificate_path
        self.private_key_path = private_key_path
        self.ca_path = ca_path

    def install(
        self,
        *,
        certificate_pem: str | None,
        ca_certificate_pem: str | None = None,
    ) -> ManagedCertificate | None:
        if not certificate_pem:
            return self.current_certificate()

        self.certificate_path.parent.mkdir(parents=True, exist_ok=True)
        self.certificate_path.write_text(certificate_pem, encoding="utf-8")
        if self.ca_path is not None and ca_certificate_pem:
            self.ca_path.parent.mkdir(parents=True, exist_ok=True)
            self.ca_path.write_text(ca_certificate_pem, encoding="utf-8")
        return self.current_certificate()

    def install_certificate_authority(self, ca_certificate_pem: str) -> Path | None:
        if self.ca_path is None:
            return None
        self.ca_path.parent.mkdir(parents=True, exist_ok=True)
        self.ca_path.write_text(ca_certificate_pem, encoding="utf-8")
        return self.ca_path

    def current_certificate(self) -> ManagedCertificate | None:
        return self.inspect_current_certificate().certificate

    def inspect_current_certificate(self) -> ManagedCertificateInspection:
        if not self.certificate_path.exists():
            return ManagedCertificateInspection(certificate=None)
        try:
            certificate = x509.load_pem_x509_certificate(self.certificate_path.read_bytes())
        except Exception as exc:
            return ManagedCertificateInspection(certificate=None, error_detail=str(exc))
        return ManagedCertificateInspection(
            certificate=ManagedCertificate(
                certificate_path=self.certificate_path,
                ca_path=self.ca_path if self.ca_path is not None and self.ca_path.exists() else None,
                not_valid_after=_normalize_datetime(certificate.not_valid_after_utc),
                subject=certificate.subject.rfc4514_string() or None,
                issuer=certificate.issuer.rfc4514_string() or None,
                serial_number=format(certificate.serial_number, "x"),
            )
        )

    def current_cert_chain(self) -> tuple[str | None, str | None, str | None]:
        certificate_path = str(self.certificate_path) if self.certificate_path.exists() else None
        key_path = str(self.private_key_path) if self.private_key_path.exists() else None
        ca_path = None
        if self.ca_path is not None and self.ca_path.exists():
            ca_path = str(self.ca_path)
        return certificate_path, key_path, ca_path

    def certificate_needs_renewal(self, *, skew_seconds: int) -> bool:
        certificate = self.current_certificate()
        if certificate is None or certificate.not_valid_after is None:
            return True
        return certificate.not_valid_after <= datetime.now(tz=UTC) + timedelta(seconds=skew_seconds)

    def clear_managed_certificate(self, *, keep_certificate_authority: bool = True) -> None:
        if self.certificate_path.exists():
            self.certificate_path.unlink()
        if not keep_certificate_authority and self.ca_path is not None and self.ca_path.exists():
            self.ca_path.unlink()


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
