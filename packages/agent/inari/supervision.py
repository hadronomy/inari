from __future__ import annotations

from .gateway.supervisor import GatewaySupervisor
from .runtime.supervisor import RuntimeSupervisor


class ApplicationSupervisor:
    def __init__(
        self,
        *,
        runtime_supervisor: RuntimeSupervisor,
        gateway_supervisor: GatewaySupervisor | None = None,
    ) -> None:
        self.runtime_supervisor = runtime_supervisor
        self.gateway_supervisor = gateway_supervisor

    async def start(self) -> None:
        await self.runtime_supervisor.start()
        if self.gateway_supervisor is not None:
            await self.gateway_supervisor.start()

    async def stop(self) -> None:
        if self.gateway_supervisor is not None:
            await self.gateway_supervisor.stop()
        await self.runtime_supervisor.stop()
