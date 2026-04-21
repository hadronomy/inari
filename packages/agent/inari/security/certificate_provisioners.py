from __future__ import annotations

import ssl
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Protocol

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from ..config import AgentSettings
from ..exceptions import AgentError
from ..gateway.models import (
    CertificateEnrollmentSpec,
    ManagedCertificateFailureReason,
    ManagedCertificateOperation,
    UpstreamCertificateMode,
)
from .certificates import CertificateLifecycleService


@dataclass(slots=True, frozen=True, kw_only=True)
class TrustBootstrapRequest:
    enrollment: CertificateEnrollmentSpec


@dataclass(slots=True, frozen=True, kw_only=True)
class CertificateEnrollmentRequest:
    enrollment: CertificateEnrollmentSpec
    csr_pem: str


@dataclass(slots=True, frozen=True, kw_only=True)
class CertificateRenewalRequest:
    enrollment: CertificateEnrollmentSpec | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ProvisionedCertificateMaterial:
    leaf_certificate_pem: str
    ca_bundle_pem: str | None = None


class ClientCertificateProvider(Protocol):
    manages_client_certificate: bool

    async def bootstrap_trust(self, request: TrustBootstrapRequest) -> None: ...

    async def enroll(
        self, request: CertificateEnrollmentRequest
    ) -> ProvisionedCertificateMaterial | None: ...

    async def renew(
        self, request: CertificateRenewalRequest
    ) -> ProvisionedCertificateMaterial | None: ...


class ManagedCertificateProvisioningError(AgentError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: ManagedCertificateOperation,
        failure_reason: ManagedCertificateFailureReason,
        retryable: bool,
        rebootstrap_required: bool = False,
        status_code: int = 502,
    ) -> None:
        super().__init__(code, message, status_code=status_code)
        self.operation = operation
        self.failure_reason = failure_reason
        self.retryable = retryable
        self.rebootstrap_required = rebootstrap_required


class RetryableProvisioningError(ManagedCertificateProvisioningError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: ManagedCertificateOperation,
        failure_reason: ManagedCertificateFailureReason,
        status_code: int = 502,
    ) -> None:
        super().__init__(
            code,
            message,
            operation=operation,
            failure_reason=failure_reason,
            retryable=True,
            status_code=status_code,
        )


class PermanentProvisioningError(ManagedCertificateProvisioningError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: ManagedCertificateOperation,
        failure_reason: ManagedCertificateFailureReason,
        rebootstrap_required: bool = False,
        status_code: int = 502,
    ) -> None:
        super().__init__(
            code,
            message,
            operation=operation,
            failure_reason=failure_reason,
            retryable=False,
            rebootstrap_required=rebootstrap_required,
            status_code=status_code,
        )


class TrustBootstrapError(PermanentProvisioningError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        rebootstrap_required: bool = False,
        status_code: int = 502,
    ) -> None:
        super().__init__(
            code,
            message,
            operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
            failure_reason=ManagedCertificateFailureReason.ROOT_FINGERPRINT_MISMATCH,
            rebootstrap_required=rebootstrap_required,
            status_code=status_code,
        )


class ReenrollmentRequiredError(PermanentProvisioningError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: ManagedCertificateOperation,
        failure_reason: ManagedCertificateFailureReason,
        status_code: int = 502,
    ) -> None:
        super().__init__(
            code,
            message,
            operation=operation,
            failure_reason=failure_reason,
            rebootstrap_required=True,
            status_code=status_code,
        )


class RenewalUnsupportedProvisioningError(PermanentProvisioningError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "CERTIFICATE_RENEWAL_UNSUPPORTED",
            message,
            operation=ManagedCertificateOperation.RENEW,
            failure_reason=ManagedCertificateFailureReason.RENEWAL_UNSUPPORTED,
        )


class DisabledCertificateProvider:
    manages_client_certificate = False

    async def bootstrap_trust(self, request: TrustBootstrapRequest) -> None:
        del request
        return None

    async def enroll(
        self, request: CertificateEnrollmentRequest
    ) -> ProvisionedCertificateMaterial | None:
        del request
        return None

    async def renew(
        self, request: CertificateRenewalRequest
    ) -> ProvisionedCertificateMaterial | None:
        del request
        return None


class ControllerCertificateProvider:
    manages_client_certificate = False

    async def bootstrap_trust(self, request: TrustBootstrapRequest) -> None:
        del request
        return None

    async def enroll(
        self, request: CertificateEnrollmentRequest
    ) -> ProvisionedCertificateMaterial | None:
        del request
        return None

    async def renew(
        self, request: CertificateRenewalRequest
    ) -> ProvisionedCertificateMaterial | None:
        del request
        return None


class StepCaCertificateProvider:
    manages_client_certificate = True

    def __init__(
        self,
        *,
        settings: AgentSettings,
        certificate_service: CertificateLifecycleService,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self.certificate_service = certificate_service
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    async def bootstrap_trust(self, request: TrustBootstrapRequest) -> None:
        enrollment = request.enrollment
        root_fingerprint = (
            enrollment.trust.root_fingerprint if enrollment.trust is not None else None
        )
        if not root_fingerprint:
            return None

        ca_path = self.certificate_service.ca_path
        if ca_path is not None and ca_path.exists():
            actual_fingerprint = None
            with suppress(Exception):
                actual_fingerprint = _certificate_fingerprint(
                    ca_path.read_text(encoding="utf-8")
                )
            if actual_fingerprint == _normalize_fingerprint(root_fingerprint):
                return None

        root_url = f"{enrollment.base_url.rstrip('/')}/1.0/root/{root_fingerprint}"
        try:
            async with self._http_client_factory(
                verify=False,
                timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as client:
                response = await client.get(root_url)
                response.raise_for_status()
                root_pem = response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ReenrollmentRequiredError(
                    "STEP_CA_ROOT_BOOTSTRAP_REJECTED",
                    f"step-ca rejected trust bootstrap with HTTP {exc.response.status_code}.",
                    operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
                    failure_reason=ManagedCertificateFailureReason.AUTH_FAILED,
                ) from exc
            raise RetryableProvisioningError(
                "STEP_CA_ROOT_UNAVAILABLE",
                f"step-ca trust bootstrap failed with HTTP {exc.response.status_code}.",
                operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
                failure_reason=ManagedCertificateFailureReason.CA_UNAVAILABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise RetryableProvisioningError(
                "STEP_CA_ROOT_UNAVAILABLE",
                f"step-ca trust bootstrap failed: {exc}",
                operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
                failure_reason=ManagedCertificateFailureReason.NETWORK_ERROR,
            ) from exc

        actual_fingerprint = _certificate_fingerprint(root_pem)
        if actual_fingerprint != _normalize_fingerprint(root_fingerprint):
            raise TrustBootstrapError(
                "STEP_CA_ROOT_FINGERPRINT_MISMATCH",
                "step-ca root certificate fingerprint did not match the controller-provided fingerprint.",
                rebootstrap_required=True,
            )
        self.certificate_service.install_certificate_authority(root_pem)

    async def enroll(
        self, request: CertificateEnrollmentRequest
    ) -> ProvisionedCertificateMaterial | None:
        bootstrap_auth = request.enrollment.bootstrap_auth
        if bootstrap_auth is None or not bootstrap_auth.token:
            return None
        try:
            async with self._http_client_factory(
                verify=self._verify_context(),
                timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as client:
                response = await client.post(
                    self._sign_url(request.enrollment),
                    json={"csr": request.csr_pem, "ott": bootstrap_auth.token},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ReenrollmentRequiredError(
                    "STEP_CA_BOOTSTRAP_REJECTED",
                    f"step-ca rejected certificate enrollment with HTTP {exc.response.status_code}.",
                    operation=ManagedCertificateOperation.ISSUE,
                    failure_reason=ManagedCertificateFailureReason.BOOTSTRAP_EXPIRED,
                ) from exc
            raise RetryableProvisioningError(
                "STEP_CA_ISSUANCE_FAILED",
                f"step-ca certificate enrollment failed with HTTP {exc.response.status_code}.",
                operation=ManagedCertificateOperation.ISSUE,
                failure_reason=ManagedCertificateFailureReason.CA_UNAVAILABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise RetryableProvisioningError(
                "STEP_CA_ISSUANCE_FAILED",
                f"step-ca certificate enrollment failed: {exc}",
                operation=ManagedCertificateOperation.ISSUE,
                failure_reason=ManagedCertificateFailureReason.NETWORK_ERROR,
            ) from exc

        certificate_pem, ca_pem = _parse_certificate_response(response)
        return _build_certificate_material(certificate_pem, ca_pem)

    async def renew(
        self, request: CertificateRenewalRequest
    ) -> ProvisionedCertificateMaterial | None:
        certificate_path, key_path, _ = self.certificate_service.current_cert_chain()
        if certificate_path is None or key_path is None:
            raise ReenrollmentRequiredError(
                "STEP_CA_LOCAL_CERTIFICATE_MISSING",
                "The managed client certificate or private key is missing locally.",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.LOCAL_CERTIFICATE_INVALID,
            )
        renew_url = self._renew_url(request.enrollment)
        if renew_url is None:
            raise RenewalUnsupportedProvisioningError(
                "No step-ca renewal endpoint is configured."
            )
        try:
            async with self._http_client_factory(
                verify=self._verify_context(),
                cert=(str(certificate_path), str(key_path)),
                timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as client:
                response = await client.post(renew_url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ReenrollmentRequiredError(
                    "STEP_CA_RENEWAL_REJECTED",
                    f"step-ca rejected certificate renewal with HTTP {exc.response.status_code}.",
                    operation=ManagedCertificateOperation.RENEW,
                    failure_reason=ManagedCertificateFailureReason.AUTH_FAILED,
                ) from exc
            raise RetryableProvisioningError(
                "STEP_CA_RENEWAL_FAILED",
                f"step-ca certificate renewal failed with HTTP {exc.response.status_code}.",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.CA_UNAVAILABLE,
            ) from exc
        except httpx.HTTPError as exc:
            raise RetryableProvisioningError(
                "STEP_CA_RENEWAL_FAILED",
                f"step-ca certificate renewal failed: {exc}",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.NETWORK_ERROR,
            ) from exc

        certificate_pem, ca_pem = _parse_certificate_response(response)
        return _build_certificate_material(certificate_pem, ca_pem)

    def _sign_url(self, enrollment: CertificateEnrollmentSpec) -> str:
        if self.settings.step_ca_sign_url:
            return self.settings.step_ca_sign_url
        return f"{enrollment.base_url.rstrip('/')}/1.0/sign"

    def _renew_url(self, enrollment: CertificateEnrollmentSpec | None) -> str | None:
        if self.settings.step_ca_renew_url:
            return self.settings.step_ca_renew_url
        if enrollment is not None:
            return f"{enrollment.base_url.rstrip('/')}/1.0/renew"
        return None

    def _verify_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if (
            self.certificate_service.ca_path is not None
            and self.certificate_service.ca_path.exists()
        ):
            context.load_verify_locations(cafile=str(self.certificate_service.ca_path))
        return context


def build_certificate_provider(
    settings: AgentSettings,
    *,
    certificate_service: CertificateLifecycleService,
    http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> ClientCertificateProvider:
    match settings.upstream_certificate_mode:
        case UpstreamCertificateMode.NONE:
            return DisabledCertificateProvider()
        case UpstreamCertificateMode.STEP_CA:
            return StepCaCertificateProvider(
                settings=settings,
                certificate_service=certificate_service,
                http_client_factory=http_client_factory,
            )
        case UpstreamCertificateMode.CONTROLLER:
            return ControllerCertificateProvider()


def _parse_certificate_response(response: httpx.Response) -> tuple[str, str | None]:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        if "BEGIN CERTIFICATE" not in text:
            raise AgentError(
                "STEP_CA_INVALID_RESPONSE",
                "step-ca returned an unsupported certificate response payload.",
                status_code=502,
            ) from None
        return text, None

    certificate_pem = (
        payload.get("crt")
        or payload.get("cert")
        or payload.get("certificate")
        or payload.get("certificate_pem")
    )
    cert_chain = payload.get("certChain") or payload.get("cert_chain")
    if not certificate_pem and isinstance(cert_chain, list) and cert_chain:
        certificate_pem = cert_chain[0]
        remaining = cert_chain[1:]
        ca_pem = "\n".join(str(item).strip() for item in remaining if item)
        return str(certificate_pem), ca_pem or None
    if not certificate_pem:
        raise AgentError(
            "STEP_CA_CERTIFICATE_MISSING",
            "step-ca did not return a signed certificate in its response payload.",
            status_code=502,
        )
    ca_pem = (
        payload.get("ca")
        or payload.get("ca_bundle")
        or payload.get("ca_certificate")
        or payload.get("ca_certificate_pem")
    )
    if not ca_pem and isinstance(cert_chain, list) and len(cert_chain) > 1:
        ca_pem = "\n".join(str(item).strip() for item in cert_chain[1:] if item)
    return str(certificate_pem), str(ca_pem) if ca_pem else None


def _build_certificate_material(
    certificate_pem: str,
    ca_pem: str | None,
) -> ProvisionedCertificateMaterial:
    # Validate provider output before the lifecycle manager attempts installation.
    x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    return ProvisionedCertificateMaterial(
        leaf_certificate_pem=certificate_pem,
        ca_bundle_pem=ca_pem,
    )


def _certificate_fingerprint(certificate_pem: str) -> str:
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    return sha256(
        certificate.public_bytes(encoding=serialization.Encoding.DER)
    ).hexdigest()


def _normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").replace(" ", "").casefold()
