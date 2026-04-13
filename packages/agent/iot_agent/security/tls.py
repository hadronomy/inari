from __future__ import annotations

import ssl
from pathlib import Path

from ..config import AgentSettings
from .certificates import CertificateLifecycleService


class TlsContextFactory:
    def __init__(
        self,
        settings: AgentSettings,
        *,
        certificate_service: CertificateLifecycleService | None = None,
    ) -> None:
        self.settings = settings
        self.certificate_service = certificate_service

    def create_outbound_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        cafile = _path_string(self.settings.tls_ca_path)
        if cafile is not None:
            context.load_verify_locations(cafile=cafile)
        if self.certificate_service is not None and self.settings.upstream_trust_client_ca:
            _, _, managed_ca_path = self.certificate_service.current_cert_chain()
            if managed_ca_path is not None:
                context.load_verify_locations(cafile=managed_ca_path)
        if self.certificate_service is not None:
            certificate_path, key_path, _ = self.certificate_service.current_cert_chain()
            if certificate_path is not None and key_path is not None:
                context.load_cert_chain(certfile=certificate_path, keyfile=key_path)
        return context

    def server_options(self) -> dict[str, str]:
        cert_path = _path_string(self.settings.tls_cert_path)
        key_path = _path_string(self.settings.tls_key_path)
        if cert_path is None or key_path is None:
            return {}
        return {
            "ssl_certfile": cert_path,
            "ssl_keyfile": key_path,
        }


def _path_string(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path)
