from __future__ import annotations

import asyncio
import ssl
from hashlib import sha256
from typing import Callable, Protocol

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from ..config import AgentSettings
from ..exceptions import AgentError
from ..gateway.models import (
    GatewayEnrollmentRecord,
    ManagedCertificateFailureReason,
    ManagedCertificateOperation,
    StepCaOttBootstrap,
    UpstreamCertificateMode,
)
from .certificates import (
    CertificateLifecycleService,
    ManagedCertificate,
    ManagedCertificateInspection,
)
from .identity import AgentIdentityService


class ClientCertificateProvisioner(Protocol):
    mode: UpstreamCertificateMode

    async def ensure_certificate(
        self, enrollment: GatewayEnrollmentRecord | None = None
    ) -> ManagedCertificate | None: ...


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


class DisabledCertificateProvisioner:
    mode = UpstreamCertificateMode.NONE

    async def ensure_certificate(
        self, enrollment: GatewayEnrollmentRecord | None = None
    ) -> ManagedCertificate | None:
        return None


class ControllerCertificateProvisioner:
    mode = UpstreamCertificateMode.CONTROLLER

    def __init__(self, *, certificate_service: CertificateLifecycleService) -> None:
        self.certificate_service = certificate_service

    async def ensure_certificate(
        self, enrollment: GatewayEnrollmentRecord | None = None
    ) -> ManagedCertificate | None:
        return self.certificate_service.current_certificate()


class StepCaOttCertificateProvisioner:
    mode = UpstreamCertificateMode.STEP_CA

    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        certificate_service: CertificateLifecycleService,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.certificate_service = certificate_service
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._lock = asyncio.Lock()

    async def ensure_certificate(
        self, enrollment: GatewayEnrollmentRecord | None = None
    ) -> ManagedCertificate | None:
        async with self._lock:
            bootstrap = (
                enrollment.certificate_bootstrap if enrollment is not None else None
            )
            if bootstrap is not None:
                await self._bootstrap_root_if_needed(bootstrap)
            inspection = self.certificate_service.inspect_current_certificate()
            current = self._coerce_current_certificate(inspection)
            if not self.certificate_service.certificate_needs_renewal(
                skew_seconds=self.settings.step_ca_certificate_renewal_skew_seconds
            ):
                return current
            if current is not None:
                renewed = await self._renew_certificate(bootstrap)
                return renewed
            if bootstrap is None or not bootstrap.ott:
                return current
            return await self._issue_certificate(bootstrap)

    async def _bootstrap_root_if_needed(self, bootstrap: StepCaOttBootstrap) -> None:
        ca_path = self.certificate_service.ca_path
        if ca_path is not None and ca_path.exists():
            try:
                actual_fingerprint = _certificate_fingerprint(
                    ca_path.read_text(encoding="utf-8")
                )
            except Exception:
                actual_fingerprint = None
            else:
                if actual_fingerprint == _normalize_fingerprint(
                    bootstrap.root_fingerprint
                ):
                    return
        root_url = (
            f"{bootstrap.ca_url.rstrip('/')}/1.0/root/{bootstrap.root_fingerprint}"
        )
        try:
            async with self._http_client_factory(
                verify=False, timeout=self.settings.gateway_reconnect_delay_seconds
            ) as client:
                response = await client.get(root_url)
                response.raise_for_status()
                root_pem = response.text
        except httpx.HTTPStatusError as exc:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_ROOT_UNAVAILABLE",
                f"step-ca rejected root bootstrap with HTTP {exc.response.status_code}.",
                operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
                failure_reason=ManagedCertificateFailureReason.CA_UNAVAILABLE,
                retryable=exc.response.status_code not in {401, 403, 404},
                rebootstrap_required=exc.response.status_code in {401, 403},
            ) from exc
        except httpx.HTTPError as exc:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_ROOT_UNAVAILABLE",
                f"step-ca root bootstrap failed: {exc}",
                operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
                failure_reason=ManagedCertificateFailureReason.NETWORK_ERROR,
                retryable=True,
            ) from exc
        actual_fingerprint = _certificate_fingerprint(root_pem)
        if actual_fingerprint != _normalize_fingerprint(bootstrap.root_fingerprint):
            raise ManagedCertificateProvisioningError(
                "STEP_CA_ROOT_FINGERPRINT_MISMATCH",
                "step-ca root certificate fingerprint did not match the controller-provided fingerprint.",
                operation=ManagedCertificateOperation.BOOTSTRAP_ROOT,
                failure_reason=ManagedCertificateFailureReason.ROOT_FINGERPRINT_MISMATCH,
                retryable=False,
                rebootstrap_required=True,
            )
        self.certificate_service.install_certificate_authority(root_pem)

    async def _issue_certificate(
        self, bootstrap: StepCaOttBootstrap
    ) -> ManagedCertificate:
        identity = self.identity_service.get_or_create_identity()
        subject = bootstrap.subject or identity.agent_id
        uri_sans = bootstrap.authorized_sans or self._requested_sans(identity.agent_id)
        csr_pem = self.identity_service.build_csr_pem(
            common_name=subject,
            uri_sans=uri_sans,
        )
        sign_url = bootstrap.sign_url or self._sign_url(bootstrap)
        try:
            async with self._http_client_factory(
                verify=self._verify_context(),
                timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as client:
                response = await client.post(
                    sign_url,
                    json={"csr": csr_pem, "ott": bootstrap.ott},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                certificate_pem, ca_pem = _parse_certificate_response(response)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ManagedCertificateProvisioningError(
                    "STEP_CA_BOOTSTRAP_REJECTED",
                    f"step-ca rejected certificate bootstrap with HTTP {exc.response.status_code}.",
                    operation=ManagedCertificateOperation.ISSUE,
                    failure_reason=ManagedCertificateFailureReason.BOOTSTRAP_EXPIRED,
                    retryable=False,
                    rebootstrap_required=True,
                ) from exc
            raise ManagedCertificateProvisioningError(
                "STEP_CA_ISSUANCE_FAILED",
                f"step-ca certificate issuance failed with HTTP {exc.response.status_code}.",
                operation=ManagedCertificateOperation.ISSUE,
                failure_reason=ManagedCertificateFailureReason.CA_UNAVAILABLE,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_ISSUANCE_FAILED",
                f"step-ca certificate issuance failed: {exc}",
                operation=ManagedCertificateOperation.ISSUE,
                failure_reason=ManagedCertificateFailureReason.NETWORK_ERROR,
                retryable=True,
            ) from exc
        installed = self.certificate_service.install(
            certificate_pem=certificate_pem,
            ca_certificate_pem=ca_pem,
        )
        if installed is None:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_CERTIFICATE_MISSING",
                "step-ca did not return a usable client certificate.",
                operation=ManagedCertificateOperation.ISSUE,
                failure_reason=ManagedCertificateFailureReason.UNKNOWN,
                retryable=False,
            )
        return installed

    async def _renew_certificate(
        self, bootstrap: StepCaOttBootstrap | None
    ) -> ManagedCertificate:
        certificate_path, key_path, _ = self.certificate_service.current_cert_chain()
        renew_url = self._renew_url(bootstrap)
        if certificate_path is None or key_path is None:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_LOCAL_CERTIFICATE_MISSING",
                "The managed client certificate or private key is missing locally.",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.LOCAL_CERTIFICATE_INVALID,
                retryable=False,
                rebootstrap_required=True,
            )
        if renew_url is None:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_RENEWAL_UNSUPPORTED",
                "No step-ca renewal endpoint is configured.",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.RENEWAL_UNSUPPORTED,
                retryable=False,
            )
        try:
            async with self._http_client_factory(
                verify=self._verify_context(),
                cert=(certificate_path, key_path),
                timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as client:
                response = await client.post(renew_url)
                response.raise_for_status()
                certificate_pem, ca_pem = _parse_certificate_response(response)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ManagedCertificateProvisioningError(
                    "STEP_CA_RENEWAL_REJECTED",
                    f"step-ca rejected certificate renewal with HTTP {exc.response.status_code}.",
                    operation=ManagedCertificateOperation.RENEW,
                    failure_reason=ManagedCertificateFailureReason.AUTH_FAILED,
                    retryable=False,
                    rebootstrap_required=True,
                ) from exc
            raise ManagedCertificateProvisioningError(
                "STEP_CA_RENEWAL_FAILED",
                f"step-ca certificate renewal failed with HTTP {exc.response.status_code}.",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.CA_UNAVAILABLE,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_RENEWAL_FAILED",
                f"step-ca certificate renewal failed: {exc}",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.NETWORK_ERROR,
                retryable=True,
            ) from exc
        renewed = self.certificate_service.install(
            certificate_pem=certificate_pem,
            ca_certificate_pem=ca_pem,
        )
        if renewed is None:
            raise ManagedCertificateProvisioningError(
                "STEP_CA_CERTIFICATE_MISSING",
                "step-ca renewal did not return a usable client certificate.",
                operation=ManagedCertificateOperation.RENEW,
                failure_reason=ManagedCertificateFailureReason.UNKNOWN,
                retryable=False,
            )
        return renewed

    def _requested_sans(self, agent_id: str) -> tuple[str, ...]:
        if self.settings.step_ca_requested_sans:
            return tuple(self.settings.step_ca_requested_sans)
        return (self.identity_service.default_uri_san(agent_id),)

    def _sign_url(self, bootstrap: StepCaOttBootstrap) -> str:
        if bootstrap.sign_url:
            return bootstrap.sign_url
        if self.settings.step_ca_sign_url:
            return self.settings.step_ca_sign_url
        return f"{bootstrap.ca_url.rstrip('/')}/1.0/sign"

    def _renew_url(self, bootstrap: StepCaOttBootstrap | None) -> str | None:
        if bootstrap is not None and bootstrap.renew_url:
            return bootstrap.renew_url
        if self.settings.step_ca_renew_url:
            return self.settings.step_ca_renew_url
        if bootstrap is not None:
            return f"{bootstrap.ca_url.rstrip('/')}/1.0/renew"
        if self.settings.step_ca_url:
            return f"{self.settings.step_ca_url.rstrip('/')}/1.0/renew"
        return None

    def _verify_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if (
            self.certificate_service.ca_path is not None
            and self.certificate_service.ca_path.exists()
        ):
            context.load_verify_locations(cafile=str(self.certificate_service.ca_path))
        return context

    def _coerce_current_certificate(
        self, inspection: ManagedCertificateInspection
    ) -> ManagedCertificate | None:
        if inspection.error_detail is None:
            return inspection.certificate
        self.certificate_service.clear_managed_certificate(
            keep_certificate_authority=True
        )
        raise ManagedCertificateProvisioningError(
            "STEP_CA_INVALID_LOCAL_CERTIFICATE",
            f"The installed managed client certificate is invalid: {inspection.error_detail}",
            operation=ManagedCertificateOperation.INSPECT,
            failure_reason=ManagedCertificateFailureReason.LOCAL_CERTIFICATE_INVALID,
            retryable=False,
            rebootstrap_required=True,
        )


def build_certificate_provisioner(
    settings: AgentSettings,
    *,
    identity_service: AgentIdentityService,
    certificate_service: CertificateLifecycleService,
    http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> ClientCertificateProvisioner:
    if settings.upstream_certificate_mode is UpstreamCertificateMode.NONE:
        return DisabledCertificateProvisioner()
    if settings.upstream_certificate_mode is UpstreamCertificateMode.STEP_CA:
        return StepCaOttCertificateProvisioner(
            settings=settings,
            identity_service=identity_service,
            certificate_service=certificate_service,
            http_client_factory=http_client_factory,
        )
    return ControllerCertificateProvisioner(certificate_service=certificate_service)


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


def _certificate_fingerprint(certificate_pem: str) -> str:
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    return sha256(
        certificate.public_bytes(encoding=serialization.Encoding.DER)
    ).hexdigest()


def _normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").replace(" ", "").casefold()
