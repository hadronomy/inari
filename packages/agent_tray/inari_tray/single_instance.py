from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_SERVER_NAME = "Inari.DeviceCenter"
_MAX_ACTIVATION_BYTES = 16 * 1024


def parse_enrollment_link(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "inari"
        or parsed.netloc != "enroll"
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Expected an inari://enroll invitation link.")
    return value


EnrollmentLink = Annotated[str, AfterValidator(parse_enrollment_link)]


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitation: EnrollmentLink | None = None
    focus: bool = True


class DeviceCenterInstance:
    """Own the per-user Device Center instance and forward later activations."""

    def __init__(self, server: QLocalServer) -> None:
        self._server = server
        self._handler: Callable[[ActivationRequest], None] | None = None
        self._pending: list[ActivationRequest] = []
        self._buffers: dict[QLocalSocket, bytearray] = {}
        server.newConnection.connect(self._accept_connections)

    @classmethod
    def acquire(cls, request: ActivationRequest) -> DeviceCenterInstance | None:
        if cls._forward(request):
            return None
        server = cls._listen()
        if server is not None:
            return cls(server)
        if cls._forward(request):
            return None
        QLocalServer.removeServer(_SERVER_NAME)
        server = cls._listen()
        if server is None:
            raise RuntimeError(
                "Unable to create the Device Center activation channel after removing a stale endpoint."
            )
        return cls(server)

    @staticmethod
    def _listen() -> QLocalServer | None:
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        return server if server.listen(_SERVER_NAME) else None

    def set_activation_handler(
        self, handler: Callable[[ActivationRequest], None]
    ) -> None:
        self._handler = handler
        pending, self._pending = self._pending, []
        for request in pending:
            handler(request)

    def close(self) -> None:
        self._server.close()
        QLocalServer.removeServer(_SERVER_NAME)

    @staticmethod
    def _forward(request: ActivationRequest) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(_SERVER_NAME)
        if not socket.waitForConnected(750):
            return False
        socket.write(request.model_dump_json().encode("utf-8") + b"\n")
        socket.flush()
        if not socket.waitForBytesWritten(750):
            raise RuntimeError(
                "The running Device Center did not accept the activation request."
            )
        socket.disconnectFromServer()
        return True

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda active=socket: self._read(active))
            socket.disconnected.connect(lambda active=socket: self._discard(active))
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(socket.readAll().data())
        if len(buffer) > _MAX_ACTIVATION_BYTES:
            socket.abort()
            return
        while b"\n" in buffer:
            line, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            try:
                request = ActivationRequest.model_validate_json(line)
            except ValidationError:
                socket.abort()
                return
            if self._handler is None:
                self._pending.append(request)
            else:
                self._handler(request)

    def _discard(self, socket: QLocalSocket) -> None:
        self._buffers.pop(socket, None)
        socket.deleteLater()
