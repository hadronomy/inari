from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .auth import (
        NoopEnrollmentAuthProvider,
        UpstreamAuthProvider,
        ZitadelServiceAccountAuthProvider,
        build_upstream_auth_provider,
    )
    from .service import GatewayEnrollmentService

__all__ = [
    "GatewayEnrollmentService",
    "NoopEnrollmentAuthProvider",
    "UpstreamAuthProvider",
    "ZitadelServiceAccountAuthProvider",
    "build_upstream_auth_provider",
]

_AUTH_EXPORTS = {
    "NoopEnrollmentAuthProvider",
    "UpstreamAuthProvider",
    "ZitadelServiceAccountAuthProvider",
    "build_upstream_auth_provider",
}


def __getattr__(name: str) -> Any:
    if name in _AUTH_EXPORTS:
        from . import auth

        value = getattr(auth, name)
    elif name == "GatewayEnrollmentService":
        from .service import GatewayEnrollmentService

        value = GatewayEnrollmentService
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
