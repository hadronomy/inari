from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import zenoh

from ...config import AgentSettings
from ...core.exceptions import AgentError
from ...security.certificates.store import CertificateLifecycleService
from ..models import (
    GatewayEnrollmentRecord,
    UpstreamCertificateMode,
    ZenohDataPlaneAuthKind,
)
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
        self._session_closed: asyncio.Event | None = None
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
        """Deliver Controller commands until this session closes.

        Closing the transport, or reopening it for a different session, ends
        the run so the caller can admit the next session again.
        """
        closed = await self._ensure_runtime_resources(enrollment)
        await on_connected()
        for command in await self._recover_commands_since(
            enrollment=enrollment,
            last_applied_controller_sequence=last_applied_controller_sequence,
        ):
            if closed.is_set():
                return
            await on_command(command)
        while (command := await self._next_command(closed)) is not None:
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
            if (
                isinstance(message, AgentRuntimeEventMessage)
                and message.event.resource_kind == "print_job"
            ):
                await self._commit_state(keyspace, message)
                continue
            key = _publication_key(keyspace, message)
            await self._put_json(key, message.model_dump(mode="json"))

    async def _commit_state(
        self, keyspace: GatewayZenohKeyspace, message: AgentRuntimeEventMessage
    ) -> None:
        envelope = message.event.payload.get("state_envelope")
        if not isinstance(envelope, str) or set(message.event.payload) != {
            "state_envelope"
        }:
            raise ValueError(
                "A managed Print Job publication requires a signed observation."
            )
        key = keyspace.state_commit()
        expected = {
            "contract_major": 1,
            "message_id": message.message_id,
            "state_envelope_sha256": hashlib.sha256(
                envelope.encode("utf-8")
            ).hexdigest(),
        }
        payload = dump_json_payload(message.model_dump(mode="json"))
        replies = await asyncio.to_thread(self._send_state_commit, key, payload)
        for reply in replies:
            sample = getattr(reply, "ok", None)
            if sample is None or str(sample.key_expr) != key:
                continue
            try:
                receipt = load_json_payload(sample.payload.to_string())
            except (ValueError, TypeError):
                continue
            if (
                isinstance(receipt, dict)
                and type(receipt.get("contract_major")) is int
                and receipt == expected
            ):
                return
        raise AgentError(
            "UPSTREAM_STATE_COMMIT_UNCONFIRMED",
            "The Controller has not confirmed storage of the Print Job observation.",
            status_code=503,
        )

    def _send_state_commit(self, key: str, payload: str) -> list[Any]:
        session = self._session
        if session is None:
            raise RuntimeError("Zenoh session is not connected.")
        return list(
            session.get(
                key,
                payload=payload,
                encoding=zenoh.Encoding.APPLICATION_JSON,
                timeout=self.settings.zenoh_query_timeout_seconds,
            )
        )

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def _ensure_runtime_resources(
        self, enrollment: GatewayEnrollmentRecord
    ) -> asyncio.Event:
        """Open the session and its command resources; return its close signal."""
        async with self._lock:
            closed = await self._ensure_session_locked(enrollment)
            if self._runtime_resources_ready:
                return closed
            self._loop = asyncio.get_running_loop()
            keyspace = self._keyspace(enrollment)
            session = self._session
            assert session is not None
            self._subscriber = await asyncio.to_thread(
                session.declare_subscriber,
                keyspace.live_commands(),
                zenoh.handlers.Callback(
                    lambda sample: self._handle_live_command_sample(
                        sample, closed=closed
                    )
                ),
            )
            self._presence_token = await asyncio.to_thread(
                session.liveliness().declare_token,
                keyspace.presence(),
            )
            self._runtime_resources_ready = True
            return closed

    async def _next_command(
        self, closed: asyncio.Event
    ) -> ControllerCommandMessage | None:
        if closed.is_set():
            return None
        command = asyncio.ensure_future(self._command_queue.get())
        stop = asyncio.ensure_future(closed.wait())
        try:
            await asyncio.wait({command, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            command.cancel()
            stop.cancel()
        if closed.is_set():
            return None
        if command.done() and not command.cancelled():
            return command.result()
        return None

    async def _recover_commands_since(
        self,
        *,
        enrollment: GatewayEnrollmentRecord,
        last_applied_controller_sequence: int | None,
    ) -> tuple[ControllerCommandMessage, ...]:
        selector = self._history_selector(
            enrollment,
            last_applied_controller_sequence=last_applied_controller_sequence,
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
        return tuple(sorted(unique_by_command.values(), key=lambda item: item.sequence))

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
    ) -> asyncio.Event:
        fingerprint = self._session_fingerprint(enrollment)
        if (
            self._session is not None
            and self._session_closed is not None
            and self._fingerprint == fingerprint
        ):
            return self._session_closed
        await self._close_locked()
        config = self._build_config(enrollment)
        self._session = await asyncio.to_thread(self._session_open, config)
        self._fingerprint = fingerprint
        self._session_closed = asyncio.Event()
        return self._session_closed

    async def _close_locked(self) -> None:
        subscriber = self._subscriber
        presence_token = self._presence_token
        session = self._session
        session_closed = self._session_closed
        self._subscriber = None
        self._presence_token = None
        self._session = None
        self._session_closed = None
        self._fingerprint = None
        self._runtime_resources_ready = False
        if session_closed is not None:
            session_closed.set()
        while not self._command_queue.empty():
            self._command_queue.get_nowait()
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

    def _handle_live_command_sample(
        self, sample: Any, *, closed: asyncio.Event
    ) -> None:
        if closed.is_set():
            return
        if getattr(sample, "kind", None) != zenoh.SampleKind.PUT:
            return
        try:
            payload = load_json_payload(sample.payload.to_string())
            command = CONTROLLER_COMMAND_ADAPTER.validate_python(payload)
        except Exception:
            logger.exception("Failed to decode live Zenoh controller command")
            return
        if self._loop is None:
            return

        def deliver() -> None:
            if not closed.is_set():
                self._command_queue.put_nowait(command)

        self._loop.call_soon_threadsafe(deliver)

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
        config.insert_json5(
            "mode", json.dumps(enrollment.data_plane.session_mode.value)
        )
        config.insert_json5(
            "connect/endpoints",
            dump_json_payload(list(enrollment.data_plane.connect_endpoints)),
        )
        root_ca = self._root_ca_path(enrollment)
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

    def _root_ca_path(self, enrollment: GatewayEnrollmentRecord) -> Path | None:
        _, _, managed_ca_path = self.certificate_service.current_cert_chain()
        if enrollment.certificate_mode is UpstreamCertificateMode.STEP_CA:
            if managed_ca_path is None:
                raise AgentError(
                    "UPSTREAM_CA_MISSING",
                    "The managed data plane requires its pinned CA root.",
                    status_code=503,
                )
            return managed_ca_path
        if managed_ca_path is not None and self.settings.upstream_trust_client_ca:
            return managed_ca_path
        return self.settings.tls_ca_path

    def _session_fingerprint(
        self, enrollment: GatewayEnrollmentRecord
    ) -> tuple[object, ...]:
        cert_path, key_path, ca_path = self.certificate_service.current_cert_chain()
        certificate = self.certificate_service.current_certificate()
        return (
            enrollment.data_plane.session_mode.value,
            tuple(enrollment.data_plane.connect_endpoints),
            enrollment.data_plane.namespace,
            enrollment.data_plane.auth_kind.value,
            enrollment.data_plane.close_link_on_expiration,
            str(cert_path) if cert_path is not None else None,
            str(key_path) if key_path is not None else None,
            str(ca_path) if ca_path is not None else None,
            certificate.serial_number if certificate is not None else None,
            str(self.settings.tls_ca_path)
            if self.settings.tls_ca_path is not None
            else None,
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
