from __future__ import annotations

from dataclasses import dataclass

from ..config import AgentSettings
from .models import MutualTlsMode, UpstreamCertificateMode, UpstreamEdgeProvider


@dataclass(slots=True, frozen=True)
class CaddyControllerProfile:
    enabled: bool
    mutual_tls_mode: MutualTlsMode

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> CaddyControllerProfile:
        return cls(
            enabled=settings.upstream_edge_provider is UpstreamEdgeProvider.CADDY,
            mutual_tls_mode=settings.upstream_mutual_tls_mode,
        )

    def requires_client_certificate(self) -> bool:
        return self.enabled and self.mutual_tls_mode is MutualTlsMode.REQUIRED

    def render_example(
        self,
        *,
        server_name: str = "controller.example.com",
        upstream: str = "127.0.0.1:8080",
        trusted_ca_file: str = "/etc/caddy/step-ca-root.pem",
    ) -> str:
        if not self.enabled:
            return ""
        client_auth_block = ""
        if self.mutual_tls_mode is not MutualTlsMode.DISABLED:
            mode = "require_and_verify" if self.mutual_tls_mode is MutualTlsMode.REQUIRED else "verify_if_given"
            client_auth_block = (
                "    tls {\n"
                "        client_auth {\n"
                f"            mode {mode}\n"
                f"            trusted_ca_cert_file {trusted_ca_file}\n"
                "        }\n"
                "    }\n"
            )
        return (
            f"{server_name} {{\n"
            f"{client_auth_block}"
            f"    reverse_proxy {upstream}\n"
            "}\n"
        )


def validate_caddy_profile(settings: AgentSettings) -> None:
    profile = CaddyControllerProfile.from_settings(settings)
    if not profile.enabled:
        return
    if not (settings.upstream_base_url or "").startswith("https://"):
        raise RuntimeError("Caddy edge mode requires an HTTPS upstream base URL.")
    if not profile.requires_client_certificate():
        return
    if settings.upstream_certificate_mode is UpstreamCertificateMode.NONE:
        raise RuntimeError("Caddy mTLS mode requires a managed client certificate flow.")
    certificate_path = settings.security_state_dir / "upstream-client-cert.pem"
    if certificate_path.exists():
        return
    if settings.upstream_enrollment_url is None:
        raise RuntimeError(
            "Caddy mTLS bootstrap requires an explicit enrollment URL that is reachable before the agent has a client certificate."
        )
