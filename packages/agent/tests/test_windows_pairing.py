from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from inari.host_service import windows_pairing


@pytest.fixture
def pywin32(mocker):
    """Stand in for the pywin32 modules the pairing server imports on demand."""

    token = mocker.MagicMock()
    token.__int__.return_value = 4242
    modules = {
        "win32api": SimpleNamespace(
            GetCurrentThread=mocker.Mock(return_value="thread")
        ),
        "win32security": SimpleNamespace(
            TOKEN_QUERY=8,
            ImpersonateNamedPipeClient=mocker.Mock(),
            OpenThreadToken=mocker.Mock(return_value=token),
            RevertToSelf=mocker.Mock(),
        ),
    }
    mocker.patch.dict(sys.modules, modules)
    return SimpleNamespace(token=token, **modules)


def test_identifies_the_pairing_client_from_the_token_it_supplies(pywin32, mocker):
    resolve = mocker.patch.object(
        windows_pairing,
        "package_family_for_token",
        return_value="Inari.DeviceCenter_rstr038xqpvrg",
    )

    family = windows_pairing._client_package_family("pipe")

    assert family == "Inari.DeviceCenter_rstr038xqpvrg"
    pywin32.win32security.ImpersonateNamedPipeClient.assert_called_once_with("pipe")
    # The agent runs as LocalService. Reading the caller's identity has to work
    # without any right over the caller's process, so the token has to come
    # from the pipe rather than from a process the service cannot open.
    resolve.assert_called_once_with(4242)
    pywin32.win32security.OpenThreadToken.assert_called_once_with("thread", 8, True)
    pywin32.token.Close.assert_called_once_with()
    pywin32.win32security.RevertToSelf.assert_called_once_with()


def test_stops_impersonating_when_the_token_cannot_be_read(pywin32, mocker):
    mocker.patch.object(
        windows_pairing,
        "package_family_for_token",
        side_effect=OSError(5, "Windows API call failed with error 5."),
    )

    with pytest.raises(OSError):
        windows_pairing._client_package_family("pipe")

    # A service thread left impersonating a client keeps that client's rights
    # for whatever it does next, so the revert matters more than the failure.
    pywin32.win32security.RevertToSelf.assert_called_once_with()
    pywin32.token.Close.assert_called_once_with()


def test_reads_the_request_before_impersonating_the_caller(pywin32, mocker):
    """Impersonation borrows the context of the last message read from the pipe.

    A message-mode pipe with nothing read yet has no context to borrow, so
    impersonating first fails with ERROR_CANNOT_IMPERSONATE and every pairing
    attempt dies exactly where the old process lookup used to.
    """

    mocker.patch.object(windows_pairing.sys, "platform", "win32")
    order: list[str] = []
    pywin32.win32security.ImpersonateNamedPipeClient.side_effect = lambda *_: (
        order.append("impersonate")
    )

    def read(*_):
        order.append("read")
        return 0, b"\x01"

    mocker.patch.dict(
        sys.modules,
        {
            "win32file": SimpleNamespace(
                ReadFile=mocker.Mock(side_effect=read),
                WriteFile=mocker.Mock(),
                FlushFileBuffers=mocker.Mock(),
            )
        },
    )
    mocker.patch.object(
        windows_pairing, "package_family_for_token", return_value="Inari.DeviceCenter_x"
    )
    trust = mocker.Mock()
    trust.start_native_pairing.return_value = SimpleNamespace(
        secret="pairing-secret", expires_at=datetime(2026, 8, 26, tzinfo=timezone.utc)
    )
    server = windows_pairing.WindowsPairingBootstrapServer(
        trust, package_family="Inari.DeviceCenter_x"
    )

    server._serve_client("pipe")

    assert order == ["read", "impersonate"]
    trust.start_native_pairing.assert_called_once_with()


def test_refuses_a_caller_outside_the_package_without_minting_a_secret(pywin32, mocker):
    mocker.patch.object(windows_pairing.sys, "platform", "win32")
    mocker.patch.dict(
        sys.modules,
        {
            "win32file": SimpleNamespace(
                ReadFile=mocker.Mock(return_value=(0, b"\x01")),
                WriteFile=mocker.Mock(),
                FlushFileBuffers=mocker.Mock(),
            )
        },
    )
    mocker.patch.object(
        windows_pairing, "package_family_for_token", return_value="Some.Other_app"
    )
    trust = mocker.Mock()
    server = windows_pairing.WindowsPairingBootstrapServer(
        trust, package_family="Inari.DeviceCenter_x"
    )

    with pytest.raises(PermissionError):
        server._serve_client("pipe")

    trust.start_native_pairing.assert_not_called()
