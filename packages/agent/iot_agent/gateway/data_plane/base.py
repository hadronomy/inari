from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from ..models import GatewayEnrollmentRecord
from ..protocol import (
    AgentPublicationMessage,
    AgentStatusSnapshotMessage,
    ControllerCommandMessage,
)


class GatewayDataPlaneTransport(Protocol):
    async def run_forever(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        last_applied_controller_sequence: int | None,
        on_connected: Callable[[], Awaitable[None]],
        on_command: Callable[[ControllerCommandMessage], Awaitable[None]],
    ) -> None: ...

    async def publish_status(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        message: AgentStatusSnapshotMessage,
    ) -> None: ...

    async def publish_publications(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        messages: Sequence[AgentPublicationMessage],
    ) -> None: ...

    async def close(self) -> None: ...
