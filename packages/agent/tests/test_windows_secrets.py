from __future__ import annotations

import sys
from types import ModuleType

import pytest

from inari.security.windows_secrets import WindowsMachineSecretStore, _apply_service_acl


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
    # Mirrors pywin32: the ACL alone, not the Win32 out-parameter triple.
    def GetSecurityDescriptorDacl(self):
        return object()


def test_a_descriptor_without_a_dacl_is_refused(monkeypatch, tmp_path) -> None:
    """A None DACL means the file would inherit whatever the parent allows.

    Writing machine-scope credentials under a permissive DACL is worse than
    not writing them, so the store refuses rather than falling back.
    """

    win32security = ModuleType("win32security")
    setattr(win32security, "SDDL_REVISION_1", 1)
    setattr(
        win32security,
        "ConvertStringSecurityDescriptorToSecurityDescriptor",
        lambda *_: _DaclFreeSecurityDescriptor(),
    )
    monkeypatch.setitem(sys.modules, "win32security", win32security)

    with pytest.raises(RuntimeError, match="DACL is missing"):
        _apply_service_acl(tmp_path / "service-secrets.dpapi")


class _DaclFreeSecurityDescriptor:
    def GetSecurityDescriptorDacl(self):
        return None
