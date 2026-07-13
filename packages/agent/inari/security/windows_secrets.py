from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

from pydantic import RootModel

_CRYPTPROTECT_LOCAL_MACHINE = 0x4
_SECRET_FILE_SDDL = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFW;;;LS)"


class SecretPayload(RootModel[dict[str, str]]):
    pass


class WindowsMachineSecretStore:
    """Protect service credentials with machine-scoped DPAPI and a narrow DACL."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if sys.platform != "win32":
            raise RuntimeError("Windows machine secret storage requires Windows.")

    def get_secret(self, key: str) -> str | None:
        return self._load().get(key)

    def set_secret(self, key: str, value: str) -> None:
        payload = self._load()
        payload[key] = value
        self._save(payload)

    def delete_secret(self, key: str) -> None:
        payload = self._load()
        if key not in payload:
            return
        del payload[key]
        self._save(payload)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        win32crypt = importlib.import_module("win32crypt")
        _, plaintext = win32crypt.CryptUnprotectData(
            self.path.read_bytes(),
            None,
            None,
            None,
            0,
        )
        return SecretPayload.model_validate_json(bytes(plaintext)).root

    def _save(self, payload: dict[str, str]) -> None:
        win32crypt = importlib.import_module("win32crypt")
        plaintext = SecretPayload(payload).model_dump_json().encode("utf-8")
        encrypted = win32crypt.CryptProtectData(
            plaintext,
            "Inari agent service credentials",
            None,
            None,
            None,
            _CRYPTPROTECT_LOCAL_MACHINE,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(encrypted)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, self.path)
        _apply_service_acl(self.path)


def _apply_service_acl(path: Path) -> None:
    win32security = importlib.import_module("win32security")
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        _SECRET_FILE_SDDL,
        win32security.SDDL_REVISION_1,
    )
    dacl_present, dacl, _ = descriptor.GetSecurityDescriptorDacl()
    if not dacl_present:
        raise RuntimeError("The Windows service secret DACL is missing.")
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )
