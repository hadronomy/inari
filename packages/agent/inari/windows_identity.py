from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol

_APPMODEL_ERROR_NO_PACKAGE = 15_700
_ERROR_INSUFFICIENT_BUFFER = 122
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


def package_family_for_process(process_id: int) -> str | None:
    """Return the Windows package family for a process visible to this service."""

    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process: _WindowsFunction = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    close_handle: _WindowsFunction = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    process = open_process(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        process_id,
    )
    if not process:
        raise _windows_error(ctypes.get_last_error())
    try:
        function: _WindowsFunction = kernel32.GetPackageFamilyName
        function.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.UINT),
            wintypes.LPWSTR,
        ]
        function.restype = wintypes.LONG
        return _read_package_family(function, process)
    finally:
        close_handle(process)


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
    if result != 0:
        raise _windows_error(result)
    return buffer.value


def _windows_error(code: int) -> OSError:
    return OSError(code, f"Windows API call failed with error {code}.")
