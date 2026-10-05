from __future__ import annotations

from ..config import AgentSettings
from ..security.models import GatewayMode
from .onboarding import read_onboarding_record


class DeviceSharingPolicy:
    """Read the operator's current sharing selection for this Controller."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def shared_device_ids(self) -> frozenset[str]:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            return frozenset()
        record = read_onboarding_record(
            self.settings.resolved_security_state_dir / "onboarding.json"
        )
        if (
            record.devices_confirmed_at is None
            or record.controller_url is None
            or record.controller_url != self.settings.upstream_base_url
        ):
            return frozenset()
        return frozenset(record.confirmed_device_ids)

    def is_shared(self, device_id: str) -> bool:
        return device_id in self.shared_device_ids()
