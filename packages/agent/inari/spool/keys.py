from __future__ import annotations

import base64
import re
from secrets import token_bytes
from typing import Final, Protocol


class ProtectedRootSecretStore(Protocol):
    """Protected storage capability required by the root-key adapter."""

    def get_secret(self, key: str) -> str | None: ...

    def set_secret(self, key: str, value: str) -> None: ...

    def delete_secret(self, key: str) -> None: ...


_ROOT_KEY_BYTES: Final = 32
_ROOT_KEY_PREFIX: Final = "inari/device-spool/root-key"
_SELF_TEST_PREFIX: Final = "inari/device-spool/self-test"
_UNAVAILABLE_MESSAGE: Final = "Protected spool key storage is unavailable."
_BASE64URL_RE: Final = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_ROOT_KEY_VERSION: Final = 0xFFFFFFFF


class SpoolRootKeyUnavailable(RuntimeError):
    """Raised when protected storage cannot safely hold a spool root key."""

    def __init__(self) -> None:
        super().__init__(_UNAVAILABLE_MESSAGE)


class SpoolRootKeyAlreadyExists(RuntimeError):
    """Raised when a root key version is already present."""

    def __init__(self) -> None:
        super().__init__("Spool root key version already exists.")


class SpoolRootKeyService:
    """Store versioned spool root keys in the protected secret store.

    The adapter exposes key bytes to the spool cryptography boundary only. It
    never exposes the encoded secret value, and it has no fallback store of its
    own. A self-test writes and reads a probe secret, then proves that cleanup
    removed it before the Agent accepts the store as usable.

    Callers must serialize version allocation through the spool database. The
    protected store has no compare-and-set operation, so the read-before-write
    guard cannot resolve two concurrent writers on its own.
    """

    def __init__(self, secret_store: ProtectedRootSecretStore) -> None:
        self._secret_store = secret_store

    def read(self, version: int) -> bytes | None:
        name = _root_secret_name(version)
        value = self._call_get(name)
        if value is None:
            return None
        return _decode_root_key(value)

    def write(self, version: int, key: bytes) -> None:
        name = _root_secret_name(version)
        _validate_key(key)
        if self.read(version) is not None:
            raise SpoolRootKeyAlreadyExists()
        encoded = _encode_root_key(key)
        self._call_set(name, encoded)
        if self.read(version) != key:
            raise SpoolRootKeyUnavailable()

    def delete(self, version: int) -> None:
        self._call_delete(_root_secret_name(version))
        if self._call_get(_root_secret_name(version)) is not None:
            raise SpoolRootKeyUnavailable()

    def self_test(self) -> None:
        """Prove that protected storage supports an isolated write/read/delete."""

        name = f"{_SELF_TEST_PREFIX}/v1/{_random_bytes(16).hex()}"
        key = _random_bytes(_ROOT_KEY_BYTES)
        try:
            self._call_set(name, _encode_root_key(key))
            if _decode_root_key(self._call_get_required(name)) != key:
                raise SpoolRootKeyUnavailable()
        except SpoolRootKeyUnavailable:
            raise
        except Exception:
            raise SpoolRootKeyUnavailable() from None
        finally:
            self._cleanup_probe(name)

    def generate_current(self, version: int) -> bytes:
        """Create and persist a new current root key after a successful test."""

        if self.read(version) is not None:
            raise SpoolRootKeyAlreadyExists()
        self.self_test()
        key = _random_bytes(_ROOT_KEY_BYTES)
        self.write(version, key)
        return key

    def _call_get(self, name: str) -> str | None:
        try:
            return self._secret_store.get_secret(name)
        except Exception:
            raise SpoolRootKeyUnavailable() from None

    def _call_get_required(self, name: str) -> str:
        value = self._call_get(name)
        if value is None:
            raise SpoolRootKeyUnavailable()
        return value

    def _call_set(self, name: str, value: str) -> None:
        try:
            self._secret_store.set_secret(name, value)
        except Exception:
            raise SpoolRootKeyUnavailable() from None

    def _call_delete(self, name: str) -> None:
        try:
            self._secret_store.delete_secret(name)
        except Exception:
            raise SpoolRootKeyUnavailable() from None

    def _cleanup_probe(self, name: str) -> None:
        try:
            self._secret_store.delete_secret(name)
            if self._secret_store.get_secret(name) is not None:
                raise SpoolRootKeyUnavailable()
        except SpoolRootKeyUnavailable:
            raise
        except Exception:
            raise SpoolRootKeyUnavailable() from None


def _root_secret_name(version: int) -> str:
    _validate_version(version)
    return f"{_ROOT_KEY_PREFIX}/v{version}"


def _random_bytes(length: int) -> bytes:
    try:
        return token_bytes(length)
    except Exception:
        raise SpoolRootKeyUnavailable() from None


def _validate_version(version: int) -> None:
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= _MAX_ROOT_KEY_VERSION
    ):
        raise ValueError(
            "Spool root key version must fit in an unsigned 32-bit integer."
        )


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != _ROOT_KEY_BYTES:
        raise ValueError("Spool root key must contain exactly 32 bytes.")


def _encode_root_key(key: bytes) -> str:
    _validate_key(key)
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def _decode_root_key(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != 43
        or not _BASE64URL_RE.fullmatch(value)
    ):
        raise SpoolRootKeyUnavailable()
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except Exception:
        raise SpoolRootKeyUnavailable() from None
    if len(decoded) != _ROOT_KEY_BYTES or _encode_root_key(decoded) != value:
        raise SpoolRootKeyUnavailable()
    return decoded
