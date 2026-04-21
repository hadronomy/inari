from __future__ import annotations

import ipaddress

from starlette.requests import HTTPConnection

from ..config import AgentSettings
from ..core.exceptions import AgentError
from ..gateway.edge.caddy import validate_caddy_profile
from ..gateway.models import UpstreamAuthMode, UpstreamCertificateMode
from .models import GatewayExposure, GatewaySecurityPolicy, GatewayMode

TEST_LOOPBACK_HOSTS = {"testclient", "testserver"}


class SecurityPolicyService:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.policy = GatewaySecurityPolicy(
            mode=settings.gateway_mode,
            exposure=settings.gateway_exposure,
            require_auth=True,
            require_tls=settings.gateway_exposure is GatewayExposure.LAN,
            allow_loopback_bootstrap=settings.allow_loopback_bootstrap,
            trusted_hosts=tuple(settings.trusted_hosts),
        )

    def validate_startup(self) -> None:
        host = self.settings.host
        if self.policy.exposure is GatewayExposure.LOOPBACK and not is_loopback_host(
            host
        ):
            raise RuntimeError(
                "Loopback exposure requires binding the agent to a loopback host."
            )
        if self.policy.exposure is GatewayExposure.LAN:
            if not self.settings.tls_cert_path or not self.settings.tls_key_path:
                raise RuntimeError(
                    "LAN exposure requires TLS certificate and key paths."
                )
        if (
            self.policy.mode is GatewayMode.MANAGED
            and not self.settings.upstream_base_url
        ):
            raise RuntimeError("Managed gateway mode requires an upstream base URL.")
        if (
            self.policy.mode is GatewayMode.MANAGED
            and self.settings.upstream_certificate_mode is UpstreamCertificateMode.NONE
        ):
            raise RuntimeError(
                "Managed gateway mode requires a certificate mode of 'controller' or 'step_ca' for the Zenoh data plane."
            )
        validate_caddy_profile(self.settings)
        if self.settings.upstream_auth_mode is UpstreamAuthMode.ZITADEL_SERVICE_ACCOUNT:
            if self.settings.zitadel_service_account_key_path is None and (
                self.settings.zitadel_service_user_id is None
                or self.settings.zitadel_key_id is None
                or self.settings.zitadel_private_key_path is None
            ):
                raise RuntimeError(
                    "ZITADEL auth mode requires either a service-account key file or explicit service user, key id, and private key settings."
                )
            if (
                self.settings.zitadel_base_url is None
                and self.settings.zitadel_token_url is None
            ):
                raise RuntimeError(
                    "ZITADEL auth mode requires a base URL or explicit token URL."
                )
        if self.settings.upstream_certificate_mode is UpstreamCertificateMode.STEP_CA:
            if self.policy.mode is not GatewayMode.MANAGED:
                raise RuntimeError(
                    "step-ca certificate mode requires managed gateway mode."
                )

    def assert_loopback_client(self, connection: HTTPConnection) -> None:
        client = connection.client
        client_host = client.host if client is not None else None
        if not is_loopback_host(client_host):
            raise AgentError(
                "LOOPBACK_REQUIRED",
                "This endpoint is only available to loopback clients.",
                status_code=403,
            )

    def trusted_hosts(self) -> tuple[str, ...]:
        return self.policy.trusted_hosts


def is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    normalized = host.strip().casefold()
    if normalized in {"localhost", "127.0.0.1", "::1"} | TEST_LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
