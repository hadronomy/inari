from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...gateway.models import CertificateBootstrapAuthType
    from .crypto import ManagedCertificateCryptoService
    from .lifecycle import ManagedCertificateLifecycleManager
    from .providers import (
        CertificateEnrollmentRequest,
        CertificateRenewalRequest,
        ClientCertificateProvider,
        ControllerCertificateProvider,
        DisabledCertificateProvider,
        ManagedCertificateProvisioningError,
        PermanentProvisioningError,
        ProvisionedCertificateMaterial,
        ReenrollmentRequiredError,
        RenewalUnsupportedProvisioningError,
        RetryableProvisioningError,
        StepCaCertificateProvider,
        TrustBootstrapError,
        TrustBootstrapRequest,
        build_certificate_provider,
    )
    from .store import CertificateLifecycleService, ManagedCertificate

__all__ = [
    "CertificateBootstrapAuthType",
    "CertificateEnrollmentRequest",
    "CertificateLifecycleService",
    "CertificateRenewalRequest",
    "ClientCertificateProvider",
    "ControllerCertificateProvider",
    "DisabledCertificateProvider",
    "ManagedCertificate",
    "ManagedCertificateCryptoService",
    "ManagedCertificateLifecycleManager",
    "ManagedCertificateProvisioningError",
    "PermanentProvisioningError",
    "ProvisionedCertificateMaterial",
    "ReenrollmentRequiredError",
    "RenewalUnsupportedProvisioningError",
    "RetryableProvisioningError",
    "StepCaCertificateProvider",
    "TrustBootstrapError",
    "TrustBootstrapRequest",
    "build_certificate_provider",
]

_PROVIDER_EXPORTS = {
    "CertificateEnrollmentRequest",
    "CertificateRenewalRequest",
    "ClientCertificateProvider",
    "ControllerCertificateProvider",
    "DisabledCertificateProvider",
    "ManagedCertificateProvisioningError",
    "PermanentProvisioningError",
    "ProvisionedCertificateMaterial",
    "ReenrollmentRequiredError",
    "RenewalUnsupportedProvisioningError",
    "RetryableProvisioningError",
    "StepCaCertificateProvider",
    "TrustBootstrapError",
    "TrustBootstrapRequest",
    "build_certificate_provider",
}
_STORE_EXPORTS = {"CertificateLifecycleService", "ManagedCertificate"}


def __getattr__(name: str) -> Any:
    if name == "CertificateBootstrapAuthType":
        from ...gateway.models import CertificateBootstrapAuthType

        value = CertificateBootstrapAuthType
    elif name == "ManagedCertificateCryptoService":
        from .crypto import ManagedCertificateCryptoService

        value = ManagedCertificateCryptoService
    elif name == "ManagedCertificateLifecycleManager":
        from .lifecycle import ManagedCertificateLifecycleManager

        value = ManagedCertificateLifecycleManager
    elif name in _PROVIDER_EXPORTS:
        from . import providers

        value = getattr(providers, name)
    elif name in _STORE_EXPORTS:
        from . import store

        value = getattr(store, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
