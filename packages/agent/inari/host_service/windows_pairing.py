from __future__ import annotations

import importlib
import logging
import sys
import threading
from typing import Any

from ..security.local_trust import StandaloneTrustService
from ..security.local_trust.native_bootstrap import (
    WINDOWS_PAIRING_PIPE,
    NativePairingResponse,
)
from ..windows_identity import current_package_family_name, package_family_for_process

logger = logging.getLogger(__name__)

_BUFFER_SIZE = 4_096
_PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;LS)(A;;GRGW;;;AU)"


class WindowsPairingBootstrapServer:
    """Issue one-use pairing material only to the sibling packaged tray."""

    def __init__(
        self,
        trust_service: StandaloneTrustService,
        *,
        package_family: str,
    ) -> None:
        self._trust_service = trust_service
        self._package_family = package_family
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        if sys.platform != "win32":
            raise RuntimeError("The Windows pairing bootstrap requires Windows.")

    @classmethod
    def for_current_package(
        cls,
        trust_service: StandaloneTrustService,
    ) -> WindowsPairingBootstrapServer | None:
        package_family = current_package_family_name()
        if package_family is None:
            return None
        return cls(trust_service, package_family=package_family)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._serve,
            name="inari-windows-pairing",
            daemon=False,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        _wake_server()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Windows pairing bootstrap did not stop cleanly.")
            self._thread = None

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            pipe = _create_pipe()
            try:
                _connect_pipe(pipe)
                if self._stop_event.is_set():
                    return
                self._serve_client(pipe)
            except Exception:
                logger.exception("Windows pairing bootstrap request failed")
            finally:
                _close_pipe(pipe)

    def _serve_client(self, pipe: Any) -> None:
        win32pipe = importlib.import_module("win32pipe")
        process_id = int(win32pipe.GetNamedPipeClientProcessId(pipe))
        if package_family_for_process(process_id) != self._package_family:
            raise PermissionError(
                "The pairing client is not part of the Inari MSIX package."
            )
        win32file = importlib.import_module("win32file")
        _, request = win32file.ReadFile(pipe, 1)
        if bytes(request) != b"\x01":
            raise ValueError("The native pairing request is invalid.")
        pairing = self._trust_service.start_native_pairing()
        response = NativePairingResponse(
            pairing_secret=pairing.secret,
            expires_at=pairing.expires_at,
        )
        win32file.WriteFile(pipe, response.model_dump_json().encode("utf-8"))
        win32file.FlushFileBuffers(pipe)


def _create_pipe():
    pywintypes = importlib.import_module("pywintypes")
    win32pipe = importlib.import_module("win32pipe")
    win32security = importlib.import_module("win32security")
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = (
        win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            _PIPE_SDDL,
            win32security.SDDL_REVISION_1,
        )
    )
    return win32pipe.CreateNamedPipe(
        WINDOWS_PAIRING_PIPE,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE
        | win32pipe.PIPE_READMODE_MESSAGE
        | win32pipe.PIPE_WAIT
        | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
        1,
        _BUFFER_SIZE,
        _BUFFER_SIZE,
        0,
        attributes,
    )


def _connect_pipe(pipe: Any) -> None:
    pywintypes = importlib.import_module("pywintypes")
    win32pipe = importlib.import_module("win32pipe")
    try:
        win32pipe.ConnectNamedPipe(pipe, None)
    except pywintypes.error as exc:
        if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
            raise


def _close_pipe(pipe: Any) -> None:
    win32file = importlib.import_module("win32file")
    win32pipe = importlib.import_module("win32pipe")
    try:
        win32pipe.DisconnectNamedPipe(pipe)
    except Exception:
        pass
    win32file.CloseHandle(pipe)


def _wake_server() -> None:
    try:
        win32file = importlib.import_module("win32file")
        handle = win32file.CreateFile(
            WINDOWS_PAIRING_PIPE,
            win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        win32file.WriteFile(handle, b"\x00")
        win32file.CloseHandle(handle)
    except Exception:
        return
