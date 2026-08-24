from __future__ import annotations

import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass

import uvicorn

from ..application.container import AgentContainer, build_container
from ..config import AgentSettings
from .app import create_app


class ManagedUvicornServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.started_callback: Callable[[], None] | None = None

    def install_signal_handlers(
        self,
    ) -> None:  # pragma: no cover - integration behavior
        return None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self.started_callback is not None:
            self.started_callback()


@dataclass(slots=True)
class AgentServerController:
    settings: AgentSettings
    container: AgentContainer
    server: ManagedUvicornServer

    @classmethod
    def from_settings(
        cls,
        settings: AgentSettings,
        *,
        container: AgentContainer | None = None,
    ) -> AgentServerController:
        resolved_container = container or build_container(settings)
        tls_options = (
            resolved_container.tls_context_factory.server_options()
            if resolved_container.tls_context_factory is not None
            else {}
        )
        config = uvicorn.Config(
            create_app(settings=settings, container=resolved_container),
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            log_config=(
                None
                if sys.stdout is None or sys.stderr is None
                else uvicorn.config.LOGGING_CONFIG
            ),
            reload=False,
            ssl_certfile=tls_options.get("ssl_certfile"),
            ssl_keyfile=tls_options.get("ssl_keyfile"),
        )
        return cls(
            settings=settings,
            container=resolved_container,
            server=ManagedUvicornServer(config),
        )

    def run(self, *, on_started: Callable[[], None] | None = None) -> None:
        self.server.started_callback = on_started
        self.server.run()

    def request_shutdown(self) -> None:
        self.server.should_exit = True


def serve(settings: AgentSettings, *, container: AgentContainer | None = None) -> None:
    AgentServerController.from_settings(settings, container=container).run()
