from __future__ import annotations

from ..config import AgentSettings
from .connector import GatewayConnector
from ..security.identity import AgentIdentityService


class GatewayService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        connector: GatewayConnector,
    ) -> None:
        self.settings = settings
        self.identity_service = identity_service
        self.connector = connector

    def get_identity(self):
        return self.identity_service.get_or_create_identity()

    def get_upstream_status(self):
        return self.connector.current_status()
