from __future__ import annotations

import ssl
from pathlib import Path

from ..config import AgentSettings


class TlsContextFactory:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def create_outbound_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=_path_string(self.settings.tls_ca_path))
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
