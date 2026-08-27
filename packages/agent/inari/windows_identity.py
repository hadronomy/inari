from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol

_APPMODEL_ERROR_NO_PACKAGE = 15_700
_ERROR_INSUFFICIENT_BUFFER = 122


class _WindowsFunction(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, *args: object) -> int: ...


def current_package_family_name() -> str | None:
    """Return the package family Windows assigned to this process, if any."""

    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function: _WindowsFunction = kernel32.GetCurrentPackageFamilyName
    function.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    function.restype = wintypes.LONG
    return _read_package_family(function)


def package_family_for_token(token: int) -> str | None:
    """Return the package family recorded in an access token, if any.

    Callers identify a peer by its token rather than by its process id. A
    service running as LocalService holds no rights over a process owned by the
    interactive user, so opening that process to ask the same question is
    refused before the answer can be read. A token the peer already handed us
    carries the package identity and needs no rights over the peer at all.
    """

    if sys.platform != "win32":
        return None
    # kernelbase, not kernel32: kernel32 forwards most of the app-model calls
    # but not this one. The api-ms-win-appmodel-runtime contract exports it too,
    # yet PyInstaller ships its own copies of those stubs beside the frozen
    # agent, and a shadowed forwarder resolves to nothing.
    kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)
    function: _WindowsFunction = kernelbase.GetPackageFamilyNameFromToken
    function.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.UINT),
        wintypes.LPWSTR,
    ]
    function.restype = wintypes.LONG
    return _read_package_family(function, wintypes.HANDLE(token))


def _read_package_family(
    function: _WindowsFunction,
    *prefix: object,
) -> str | None:
    length = wintypes.UINT()
    result = function(*prefix, ctypes.byref(length), None)
    if result == _APPMODEL_ERROR_NO_PACKAGE:
        return None
    if result != _ERROR_INSUFFICIENT_BUFFER:
        raise _windows_error(result)
    buffer = ctypes.create_unicode_buffer(length.value)
    result = function(*prefix, ctypes.byref(length), buffer)
    if result == _APPMODEL_ERROR_NO_PACKAGE:
        return None
    if result != 0:
        raise _windows_error(result)
    return buffer.value


def _windows_error(code: int) -> OSError:
    return OSError(code, f"Windows API call failed with error {code}.")
