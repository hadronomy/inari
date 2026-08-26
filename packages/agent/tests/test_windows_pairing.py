from __future__ import annotations

import sys
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
        "win32pipe": SimpleNamespace(ImpersonateNamedPipeClient=mocker.Mock()),
        "win32security": SimpleNamespace(
            TOKEN_QUERY=8,
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
    pywin32.win32pipe.ImpersonateNamedPipeClient.assert_called_once_with("pipe")
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
