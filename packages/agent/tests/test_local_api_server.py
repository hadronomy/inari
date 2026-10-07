from __future__ import annotations

import asyncio
import sys
from asyncio import proactor_events
from types import SimpleNamespace
from typing import Any, cast

import uvicorn

from inari.config import AgentSettings
from inari.local_api.server import AgentServerController, ManagedUvicornServer


def test_server_config_supports_a_process_without_console_streams(
    mocker, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    mocker.patch(
        "inari.local_api.server.create_app",
        return_value=mocker.AsyncMock(),
    )
    container = cast(Any, SimpleNamespace(tls_context_factory=None))

    controller = AgentServerController.from_settings(
        AgentSettings(),
        container=container,
    )

    assert controller.server.config.log_config is None
    assert controller.server.config.proxy_headers is False


def test_managed_server_reports_readiness_after_uvicorn_starts(mocker) -> None:
    callback = mocker.Mock()
    server = object.__new__(ManagedUvicornServer)
    server.started = False
    server.started_callback = callback

    async def complete_startup(instance, *, sockets=None) -> None:
        instance.started = True

    mocker.patch.object(uvicorn.Server, "startup", new=complete_startup)

    asyncio.run(server.startup())

    callback.assert_called_once_with()


def test_server_shutdown_finishes_when_a_reset_connection_cannot_detach(mocker) -> None:
    mocker.patch("inari.local_api.server.create_app", return_value=mocker.AsyncMock())
    container = cast(Any, SimpleNamespace(tls_context_factory=None))
    controller = AgentServerController.from_settings(
        AgentSettings(), container=container
    )
    server = controller.server
    server.lifespan = SimpleNamespace(shutdown=mocker.AsyncMock())

    async def shutdown() -> None:
        loop = asyncio.get_running_loop()
        failures: list[dict[str, Any]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: failures.append(context))
        listener = asyncio.Server(loop, [], asyncio.Protocol, None, 100, None, None)
        socket = mocker.Mock()
        socket.fileno.return_value = 1
        socket.shutdown.side_effect = ConnectionResetError(10054, "Peer reset")
        # CPython cannot detach this transport when socket shutdown raises.
        transport = proactor_events._ProactorBasePipeTransport(
            loop,
            socket,
            mocker.Mock(spec=asyncio.StreamReaderProtocol),
            server=listener,
        )
        server.servers = [listener]
        transport.close()
        try:
            await asyncio.wait_for(server.shutdown(), timeout=7)
            assert any(
                isinstance(failure.get("exception"), ConnectionResetError)
                for failure in failures
            )
            server.lifespan.shutdown.assert_awaited_once()
        finally:
            socket.shutdown.side_effect = None
            cast(Any, transport)._call_connection_lost(None)
            await listener.wait_closed()
            loop.set_exception_handler(previous_handler)

    asyncio.run(shutdown())


def test_server_finishes_pending_requests_before_application_cleanup(mocker) -> None:
    mocker.patch("inari.local_api.server.create_app", return_value=mocker.AsyncMock())
    container = cast(Any, SimpleNamespace(tls_context_factory=None))
    controller = AgentServerController.from_settings(
        AgentSettings(), container=container
    )
    server = controller.server
    order: list[str] = []

    async def shutdown() -> None:
        async def finish_request() -> None:
            await asyncio.sleep(0.2)
            order.append("request-finished")

        async def cleanup() -> None:
            await asyncio.sleep(0)
            order.append("application-stopped")

        request = asyncio.create_task(finish_request())
        server.server_state.tasks.add(request)
        request.add_done_callback(server.server_state.tasks.discard)
        server.servers = []
        server.lifespan = SimpleNamespace(shutdown=cleanup)
        await asyncio.wait_for(server.shutdown(), timeout=2)
        assert request.done()
        assert not request.cancelled()

    asyncio.run(shutdown())

    assert order == ["request-finished", "application-stopped"]
