from __future__ import annotations

import os
import struct
from collections.abc import Callable
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .models import (
    DATA_KEY_BYTES,
    FORMAT_VERSION,
    MAX_IDENTIFIER_BYTES,
    NONCE_BYTES,
    ArtifactKind,
    EncryptedArtifact,
    WrappedDataKey,
)

MAX_NONCE_RESERVATION_ATTEMPTS = 8
_WRAPPED_DATA_KEY_BYTES = DATA_KEY_BYTES + 16
_AAD_PREFIX = b"inari-device-spool\0"
_WRAPPED_KEY_SUFFIX = b"\0wrapped-data-key"


class SpoolCryptoError(RuntimeError):
    """Base class for content-free Device Spool cryptography failures."""


class ArtifactMalformedError(SpoolCryptoError):
    def __init__(self) -> None:
        super().__init__("The spool artifact is malformed.")


class ArtifactIntegrityError(SpoolCryptoError):
    def __init__(self) -> None:
        super().__init__("The spool artifact failed integrity verification.")


class RandomSourceError(SpoolCryptoError):
    def __init__(self) -> None:
        super().__init__("The spool random source failed.")


class NonceReservationError(SpoolCryptoError):
    def __init__(self) -> None:
        super().__init__("The spool nonce could not be reserved.")


class NonceReservationUncertainError(NonceReservationError):
    """The caller cannot prove whether a nonce reservation committed."""

    def __init__(self) -> None:
        SpoolCryptoError.__init__(self, "The spool nonce result is uncertain.")


RandomBytesSource = Callable[[int], bytes]


class NonceReservation(Protocol):
    """Persist one nonce in the uniqueness scope of the active encryption key."""

    def __call__(self, nonce: bytes) -> bool:
        """Return true only after this nonce has a durable unique reservation."""


class SpoolCrypto:
    def __init__(
        self,
        *,
        nonce_bytes_source: RandomBytesSource = os.urandom,
        data_key_bytes_source: RandomBytesSource = os.urandom,
    ) -> None:
        self._nonce_bytes_source = nonce_bytes_source
        self._data_key_bytes_source = data_key_bytes_source

    def new_data_key(self) -> bytes:
        return _read_random_bytes(self._data_key_bytes_source, DATA_KEY_BYTES)

    def encrypt_artifact(
        self,
        *,
        data_key: bytes,
        job_id: str,
        intent_id: str,
        kind: ArtifactKind,
        plaintext: bytes,
        reserve_nonce: NonceReservation,
    ) -> EncryptedArtifact:
        _validate_key(data_key)
        _validate_identifier(job_id)
        _validate_identifier(intent_id)
        if not isinstance(kind, ArtifactKind) or not isinstance(plaintext, bytes):
            raise ArtifactMalformedError
        aad = artifact_aad(job_id=job_id, intent_id=intent_id, kind=kind)
        nonce = self._reserved_nonce(reserve_nonce)
        try:
            ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
        except (TypeError, ValueError):
            raise ArtifactMalformedError from None
        try:
            return EncryptedArtifact(
                format_version=FORMAT_VERSION,
                job_id=job_id,
                intent_id=intent_id,
                kind=kind,
                nonce=nonce,
                ciphertext=ciphertext,
            )
        except ValueError:
            raise ArtifactMalformedError from None

    def decrypt_artifact(
        self,
        *,
        data_key: bytes,
        artifact: EncryptedArtifact,
    ) -> bytes:
        _validate_key(data_key)
        _validate_encrypted_artifact(artifact)
        try:
            return AESGCM(data_key).decrypt(
                artifact.nonce,
                artifact.ciphertext,
                artifact_aad(
                    job_id=artifact.job_id,
                    intent_id=artifact.intent_id,
                    kind=artifact.kind,
                ),
            )
        except InvalidTag:
            raise ArtifactIntegrityError from None
        except (TypeError, ValueError):
            raise ArtifactMalformedError from None

    def wrap_data_key(
        self,
        *,
        data_key: bytes,
        root_key: bytes,
        root_key_version: int,
        job_id: str,
        intent_id: str,
        reserve_nonce: NonceReservation,
    ) -> WrappedDataKey:
        _validate_key(data_key)
        _validate_key(root_key)
        _validate_root_key_version(root_key_version)
        _validate_identifier(job_id)
        _validate_identifier(intent_id)
        aad = wrapped_key_aad(
            job_id=job_id,
            intent_id=intent_id,
            root_key_version=root_key_version,
        )
        nonce = self._reserved_nonce(reserve_nonce)
        try:
            ciphertext = AESGCM(root_key).encrypt(nonce, data_key, aad)
        except (TypeError, ValueError):
            raise ArtifactMalformedError from None
        try:
            return WrappedDataKey(
                format_version=FORMAT_VERSION,
                root_key_version=root_key_version,
                job_id=job_id,
                intent_id=intent_id,
                nonce=nonce,
                ciphertext=ciphertext,
            )
        except ValueError:
            raise ArtifactMalformedError from None

    def unwrap_data_key(
        self,
        *,
        wrapped: WrappedDataKey,
        root_key: bytes,
        job_id: str,
        intent_id: str,
    ) -> bytes:
        _validate_key(root_key)
        _validate_identifier(job_id)
        _validate_identifier(intent_id)
        _validate_wrapped_data_key(wrapped)
        if wrapped.job_id != job_id or wrapped.intent_id != intent_id:
            raise ArtifactIntegrityError
        try:
            data_key = AESGCM(root_key).decrypt(
                wrapped.nonce,
                wrapped.ciphertext,
                wrapped_key_aad(
                    job_id=job_id,
                    intent_id=intent_id,
                    root_key_version=wrapped.root_key_version,
                ),
            )
        except InvalidTag:
            raise ArtifactIntegrityError from None
        except (TypeError, ValueError):
            raise ArtifactMalformedError from None
        _validate_key(data_key)
        return data_key

    def _reserved_nonce(self, reserve_nonce: NonceReservation) -> bytes:
        if not callable(reserve_nonce):
            raise ArtifactMalformedError
        for _ in range(MAX_NONCE_RESERVATION_ATTEMPTS):
            nonce = _read_random_bytes(self._nonce_bytes_source, NONCE_BYTES)
            try:
                reserved = reserve_nonce(nonce)
            except NonceReservationUncertainError:
                raise
            except Exception:
                raise NonceReservationError from None
            if not isinstance(reserved, bool):
                raise NonceReservationError
            if reserved:
                return nonce
        raise NonceReservationError


def artifact_aad(*, job_id: str, intent_id: str, kind: ArtifactKind) -> bytes:
    _validate_identifier(job_id)
    _validate_identifier(intent_id)
    if not isinstance(kind, ArtifactKind):
        raise ArtifactMalformedError
    return _job_aad(job_id=job_id, intent_id=intent_id) + _field(kind.value)


def wrapped_key_aad(*, job_id: str, intent_id: str, root_key_version: int) -> bytes:
    _validate_identifier(job_id)
    _validate_identifier(intent_id)
    _validate_root_key_version(root_key_version)
    return (
        _job_aad(job_id=job_id, intent_id=intent_id)
        + _WRAPPED_KEY_SUFFIX
        + struct.pack(">I", root_key_version)
    )


def _job_aad(*, job_id: str, intent_id: str) -> bytes:
    return (
        _AAD_PREFIX + bytes((FORMAT_VERSION,)) + _field(job_id) + _field(intent_id)
    )


def _field(value: str) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        raise ArtifactMalformedError from None
    if not encoded or len(encoded) > MAX_IDENTIFIER_BYTES:
        raise ArtifactMalformedError
    return struct.pack(">I", len(encoded)) + encoded


def _read_random_bytes(source: RandomBytesSource, size: int) -> bytes:
    try:
        value = source(size)
    except Exception:
        raise RandomSourceError from None
    if not isinstance(value, bytes) or len(value) != size:
        raise RandomSourceError
    return value


def _validate_encrypted_artifact(artifact: EncryptedArtifact) -> None:
    if not isinstance(artifact, EncryptedArtifact):
        raise ArtifactMalformedError
    _validate_format_version(artifact.format_version)
    _validate_identifier(artifact.job_id)
    _validate_identifier(artifact.intent_id)
    if not isinstance(artifact.kind, ArtifactKind):
        raise ArtifactMalformedError
    _validate_nonce(artifact.nonce)
    if not isinstance(artifact.ciphertext, bytes) or len(artifact.ciphertext) < 16:
        raise ArtifactMalformedError


def _validate_wrapped_data_key(wrapped: WrappedDataKey) -> None:
    if not isinstance(wrapped, WrappedDataKey):
        raise ArtifactMalformedError
    _validate_format_version(wrapped.format_version)
    _validate_root_key_version(wrapped.root_key_version)
    _validate_identifier(wrapped.job_id)
    _validate_identifier(wrapped.intent_id)
    _validate_nonce(wrapped.nonce)
    if (
        not isinstance(wrapped.ciphertext, bytes)
        or len(wrapped.ciphertext) != _WRAPPED_DATA_KEY_BYTES
    ):
        raise ArtifactMalformedError


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != DATA_KEY_BYTES:
        raise ArtifactMalformedError


def _validate_nonce(nonce: bytes) -> None:
    if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
        raise ArtifactMalformedError


def _validate_identifier(identifier: str) -> None:
    if not isinstance(identifier, str) or not identifier:
        raise ArtifactMalformedError
    try:
        encoded = identifier.encode("utf-8")
    except UnicodeEncodeError:
        raise ArtifactMalformedError from None
    if len(encoded) > MAX_IDENTIFIER_BYTES or identifier in {".", ".."}:
        raise ArtifactMalformedError
    if any(character in identifier for character in ("\x00", "/", "\\")):
        raise ArtifactMalformedError
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in identifier):
        raise ArtifactMalformedError


def _validate_format_version(version: int) -> None:
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise ArtifactMalformedError


def _validate_root_key_version(version: int) -> None:
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= 0xFFFFFFFF
    ):
        raise ArtifactMalformedError
