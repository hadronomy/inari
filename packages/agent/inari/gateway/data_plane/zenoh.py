from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import zenoh

from ...config import AgentSettings
from ...exceptions import AgentError
from ...security.certificates import CertificateLifecycleService
from ..models import GatewayEnrollmentRecord, ZenohDataPlaneAuthKind
from ..protocol import (
    AGENT_PUBLICATION_ADAPTER,
    AgentCommandAcceptedMessage,
    AgentCommandRejectedMessage,
    AgentErrorMessage,
    AgentPublicationMessage,
    AgentRuntimeEventMessage,
    AgentStatusSnapshotMessage,
    CONTROLLER_COMMAND_ADAPTER,
    CONTROLLER_COMMAND_LIST_ADAPTER,
    ControllerCommandHistoryPayload,
    ControllerCommandMessage,
)
from .codecs import dump_json_payload, load_json_payload
from .keyspace import GatewayZenohKeyspace

logger = logging.getLogger(__name__)


class ZenohGatewayTransport:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        certificate_service: CertificateLifecycleService,
        session_open: Callable[[Any], Any] | None = None,
        config_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.certificate_service = certificate_service
        self._session_open = session_open or zenoh.open
        self._config_factory = config_factory or zenoh.Config
        self._session: Any | None = None
        self._subscriber: Any | None = None
        self._presence_token: Any | None = None
        self._lock = asyncio.Lock()
        self._runtime_resources_ready = False
        self._fingerprint: tuple[object, ...] | None = None
        self._command_queue: asyncio.Queue[ControllerCommandMessage] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run_forever(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        last_applied_controller_sequence: int | None,
        on_connected: Callable[[], Awaitable[None]],
        on_command: Callable[[ControllerCommandMessage], Awaitable[None]],
    ) -> None:
        await self._ensure_runtime_resources(enrollment)
        await on_connected()
        for command in await self._recover_commands_since(
            enrollment=enrollment,
            last_applied_controller_sequence=last_applied_controller_sequence,
        ):
            await on_command(command)
        while True:
            command = await self._command_queue.get()
            await on_command(command)

    async def publish_status(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        message: AgentStatusSnapshotMessage,
    ) -> None:
        await self._ensure_runtime_resources(enrollment)
        await self._put_json(
            self._keyspace(enrollment).status_latest(),
            message.model_dump(mode="json"),
        )

    async def publish_publications(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        messages: Sequence[AgentPublicationMessage],
    ) -> None:
        await self._ensure_runtime_resources(enrollment)
        keyspace = self._keyspace(enrollment)
        for message in messages:
            key = _publication_key(keyspace, message)
            await self._put_json(key, message.model_dump(mode="json"))

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def _ensure_runtime_resources(
        self, enrollment: GatewayEnrollmentRecord
    ) -> None:
        async with self._lock:
            await self._ensure_session_locked(enrollment)
            if self._runtime_resources_ready:
                return
            self._loop = asyncio.get_running_loop()
            keyspace = self._keyspace(enrollment)
            session = self._session
            assert session is not None
            self._subscriber = await asyncio.to_thread(
                session.declare_subscriber,
                keyspace.live_commands(),
                zenoh.handlers.Callback(self._handle_live_command_sample),
            )
            self._presence_token = await asyncio.to_thread(
                session.liveliness().declare_token,
                keyspace.presence(),
            )
            self._runtime_resources_ready = True

    async def _recover_commands_since(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        last_applied_controller_sequence: int | None,
    ) -> tuple[ControllerCommandMessage, ...]:
        selector = self._history_selector(
            enrollment, last_applied_controller_sequence=last_applied_controller_sequence
        )
        replies = await asyncio.to_thread(self._collect_replies, selector)
        commands: list[ControllerCommandMessage] = []
        for reply in replies:
            sample = getattr(reply, "ok", None)
            if sample is None:
                continue
            try:
                payload = load_json_payload(sample.payload.to_string())
            except Exception:
                logger.exception("Failed to decode Zenoh command history reply")
                continue
            commands.extend(_parse_history_payload(payload))
        unique_by_command: dict[str, ControllerCommandMessage] = {}
        for command in commands:
            unique_by_command[command.command_id] = command
        return tuple(
            sorted(unique_by_command.values(), key=lambda item: item.sequence)
        )

    def _collect_replies(self, selector: str) -> list[Any]:
        session = self._session
        assert session is not None
        handler = session.get(
            selector,
            timeout=self.settings.zenoh_query_timeout_seconds,
        )
        return list(handler)

    async def _ensure_session_locked(
        self, enrollment: GatewayEnrollmentRecord
    ) -> None:
        fingerprint = self._session_fingerprint(enrollment)
        if self._session is not None and self._fingerprint == fingerprint:
            return
        await self._close_locked()
        config = self._build_config(enrollment)
        self._session = await asyncio.to_thread(self._session_open, config)
        self._fingerprint = fingerprint

    async def _close_locked(self) -> None:
        subscriber = self._subscriber
        presence_token = self._presence_token
        session = self._session
        self._subscriber = None
        self._presence_token = None
        self._session = None
        self._fingerprint = None
        self._runtime_resources_ready = False
        if subscriber is not None:
            await asyncio.to_thread(subscriber.undeclare)
        if presence_token is not None:
            await asyncio.to_thread(presence_token.undeclare)
        if session is not None:
            await asyncio.to_thread(session.close)

    async def _put_json(self, key_expr: str, payload: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            raise RuntimeError("Zenoh session is not connected.")
        await asyncio.to_thread(
            session.put,
            key_expr,
            dump_json_payload(payload),
            encoding=zenoh.Encoding.APPLICATION_JSON,
        )

    def _handle_live_command_sample(self, sample: Any) -> None:
        if getattr(sample, "kind", None) is not zenoh.SampleKind.PUT:
            return
        try:
            payload = load_json_payload(sample.payload.to_string())
            command = CONTROLLER_COMMAND_ADAPTER.validate_python(payload)
        except Exception:
            logger.exception("Failed to decode live Zenoh controller command")
            return
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._command_queue.put_nowait, command)

    def _history_selector(
        self,
        enrollment: GatewayEnrollmentRecord,
        *,
        last_applied_controller_sequence: int | None,
    ) -> str:
        base = self._keyspace(enrollment).command_history()
        if last_applied_controller_sequence is None:
            return base
        return f"{base}?from_sequence={last_applied_controller_sequence + 1}"

    def _keyspace(self, enrollment: GatewayEnrollmentRecord) -> GatewayZenohKeyspace:
        return GatewayZenohKeyspace(enrollment.data_plane.namespace)

    def _build_config(self, enrollment: GatewayEnrollmentRecord):
        config = self._config_factory()
        config.insert_json5("mode", json.dumps(enrollment.data_plane.session_mode.value))
        config.insert_json5(
            "connect/endpoints",
            dump_json_payload(list(enrollment.data_plane.connect_endpoints)),
        )
        root_ca = self._root_ca_path()
        if root_ca is not None:
            config.insert_json5(
                "transport/link/tls/root_ca_certificate",
                json.dumps(str(root_ca)),
            )
        if enrollment.data_plane.close_link_on_expiration:
            config.insert_json5(
                "transport/link/tls/close_link_on_expiration",
                "true",
            )
        if enrollment.data_plane.auth_kind is ZenohDataPlaneAuthKind.MTLS:
            cert_path, key_path, _ = self.certificate_service.current_cert_chain()
            if cert_path is None or key_path is None:
                raise AgentError(
                    "UPSTREAM_CLIENT_CERTIFICATE_MISSING",
                    "Managed Zenoh transport requires a client certificate before connecting.",
                    status_code=503,
                )
            config.insert_json5("transport/link/tls/enable_mtls", "true")
            config.insert_json5(
                "transport/link/tls/connect_private_key",
                json.dumps(str(key_path)),
            )
            config.insert_json5(
                "transport/link/tls/connect_certificate",
                json.dumps(str(cert_path)),
            )
        return config

    def _root_ca_path(self) -> Path | None:
        _, _, managed_ca_path = self.certificate_service.current_cert_chain()
        if managed_ca_path is not None and self.settings.upstream_trust_client_ca:
            return managed_ca_path
        return self.settings.tls_ca_path

    def _session_fingerprint(
        self, enrollment: GatewayEnrollmentRecord
    ) -> tuple[object, ...]:
        cert_path, key_path, ca_path = self.certificate_service.current_cert_chain()
        return (
            enrollment.data_plane.session_mode.value,
            tuple(enrollment.data_plane.connect_endpoints),
            enrollment.data_plane.namespace,
            enrollment.data_plane.auth_kind.value,
            enrollment.data_plane.close_link_on_expiration,
            str(cert_path) if cert_path is not None else None,
            str(key_path) if key_path is not None else None,
            str(ca_path) if ca_path is not None else None,
            str(self.settings.tls_ca_path) if self.settings.tls_ca_path is not None else None,
        )


def _parse_history_payload(payload: Any) -> list[ControllerCommandMessage]:
    if isinstance(payload, list):
        return list(CONTROLLER_COMMAND_LIST_ADAPTER.validate_python(payload))
    if isinstance(payload, dict) and "commands" in payload:
        history = ControllerCommandHistoryPayload.model_validate(payload)
        return list(history.commands)
    return [CONTROLLER_COMMAND_ADAPTER.validate_python(payload)]


def _publication_key(
    keyspace: GatewayZenohKeyspace,
    message: AgentPublicationMessage,
) -> str:
    AGENT_PUBLICATION_ADAPTER.validate_python(message.model_dump(mode="json"))
    if isinstance(message, (AgentCommandAcceptedMessage, AgentCommandRejectedMessage)):
        return keyspace.result(message.command_id)
    if isinstance(message, AgentRuntimeEventMessage):
        return keyspace.event(message.message_id)
    if isinstance(message, AgentErrorMessage):
        return keyspace.error(message.message_id)
    if isinstance(message, AgentStatusSnapshotMessage):
        return keyspace.status_latest()
    raise TypeError(f"Unsupported publication type {type(message).__name__}.")
