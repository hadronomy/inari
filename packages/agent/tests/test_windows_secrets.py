from __future__ import annotations

import sys
from types import ModuleType

import pytest

from inari.security.windows_secrets import WindowsMachineSecretStore


def test_machine_secret_store_encrypts_and_applies_service_acl(
    monkeypatch,
    mocker,
    tmp_path,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    win32crypt = ModuleType("win32crypt")
    setattr(
        win32crypt,
        "CryptProtectData",
        lambda payload, *_: bytes(payload)[::-1],
    )
    setattr(
        win32crypt,
        "CryptUnprotectData",
        lambda payload, *_: ("", bytes(payload)[::-1]),
    )
    win32security = ModuleType("win32security")
    set_named_security = mocker.Mock()
    setattr(win32security, "SetNamedSecurityInfo", set_named_security)
    setattr(
        win32security,
        "ConvertStringSecurityDescriptorToSecurityDescriptor",
        lambda *_: _SecurityDescriptor(),
    )
    setattr(win32security, "SDDL_REVISION_1", 1)
    setattr(win32security, "SE_FILE_OBJECT", 1)
    setattr(win32security, "DACL_SECURITY_INFORMATION", 4)
    setattr(win32security, "PROTECTED_DACL_SECURITY_INFORMATION", 0x80000000)
    monkeypatch.setitem(sys.modules, "win32crypt", win32crypt)
    monkeypatch.setitem(sys.modules, "win32security", win32security)
    secret_path = tmp_path / "service-secrets.dpapi"
    store = WindowsMachineSecretStore(secret_path)

    store.set_secret("enrollment", "sensitive-value")

    assert store.get_secret("enrollment") == "sensitive-value"
    assert b"sensitive-value" not in secret_path.read_bytes()
    set_named_security.assert_called_once()

    store.delete_secret("enrollment")

    assert store.get_secret("enrollment") is None


class _SecurityDescriptor:
    def GetSecurityDescriptorDacl(self):
        return object()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI contract")
def test_native_machine_secret_store_round_trip_and_acl(tmp_path) -> None:
    import ctypes
    import win32security

    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("The service ACL requires an elevated Windows process")

    secret_path = tmp_path / "service-secrets.dpapi"
    store = WindowsMachineSecretStore(secret_path)
    store.set_secret("probe", "test-only-value")
    assert store.get_secret("probe") == "test-only-value"
    assert b"test-only-value" not in secret_path.read_bytes()

    descriptor = win32security.GetNamedSecurityInfo(
        str(secret_path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert dacl is not None
    assert {
        win32security.ConvertSidToStringSid(dacl.GetAce(index)[2])
        for index in range(dacl.GetAceCount())
    } == {"S-1-5-18", "S-1-5-32-544", "S-1-5-19"}
    control, _ = descriptor.GetSecurityDescriptorControl()
    assert control & win32security.SE_DACL_PROTECTED

    store.delete_secret("probe")
    assert store.get_secret("probe") is None
