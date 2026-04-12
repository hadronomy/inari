from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError, NoKeyringError


class SecretStore(Protocol):
    def get_secret(self, key: str) -> str | None: ...

    def set_secret(self, key: str, value: str) -> None: ...

    def delete_secret(self, key: str) -> None: ...


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get_secret(self, key: str) -> str | None:
        return self._values.get(key)

    def set_secret(self, key: str, value: str) -> None:
        self._values[key] = value

    def delete_secret(self, key: str) -> None:
        self._values.pop(key, None)


class FileSecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_secret(self, key: str) -> str | None:
        return self._load().get(key)

    def set_secret(self, key: str, value: str) -> None:
        payload = self._load()
        payload[key] = value
        self._save(payload)

    def delete_secret(self, key: str) -> None:
        payload = self._load()
        if key in payload:
            del payload[key]
            self._save(payload)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class KeyringSecretStore:
    def __init__(self, *, service_name: str) -> None:
        self.service_name = service_name

    def get_secret(self, key: str) -> str | None:
        return keyring.get_password(self.service_name, key)

    def set_secret(self, key: str, value: str) -> None:
        keyring.set_password(self.service_name, key, value)

    def delete_secret(self, key: str) -> None:
        try:
            keyring.delete_password(self.service_name, key)
        except keyring.errors.PasswordDeleteError:
            return


class ResilientSecretStore:
    def __init__(self, *, primary: SecretStore, fallback: SecretStore) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_secret(self, key: str) -> str | None:
        try:
            value = self.primary.get_secret(key)
        except (KeyringError, NoKeyringError):
            value = None
        if value is not None:
            return value
        return self.fallback.get_secret(key)

    def set_secret(self, key: str, value: str) -> None:
        self.fallback.set_secret(key, value)
        try:
            self.primary.set_secret(key, value)
        except (KeyringError, NoKeyringError):
            return

    def delete_secret(self, key: str) -> None:
        self.fallback.delete_secret(key)
        try:
            self.primary.delete_secret(key)
        except (KeyringError, NoKeyringError):
            return
