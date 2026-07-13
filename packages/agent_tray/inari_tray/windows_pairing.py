from __future__ import annotations

import importlib
import sys

from inari.security.local_trust.native_bootstrap import (
    WINDOWS_PAIRING_PIPE,
    NativePairingResponse,
)


class WindowsPairingBootstrapClient:
    """Request pairing material through the package-authenticated service pipe."""

    def request(self) -> NativePairingResponse:
        if sys.platform != "win32":
            raise RuntimeError("The installed pairing bootstrap requires Windows.")
        win32file = importlib.import_module("win32file")
        win32pipe = importlib.import_module("win32pipe")
        win32pipe.WaitNamedPipe(WINDOWS_PAIRING_PIPE, 2_000)
        handle = win32file.CreateFile(
            WINDOWS_PAIRING_PIPE,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        try:
            win32pipe.SetNamedPipeHandleState(
                handle,
                win32pipe.PIPE_READMODE_MESSAGE,
                None,
                None,
            )
            win32file.WriteFile(handle, b"\x01")
            _, payload = win32file.ReadFile(handle, 4_096)
            return NativePairingResponse.model_validate_json(bytes(payload))
        finally:
            win32file.CloseHandle(handle)
