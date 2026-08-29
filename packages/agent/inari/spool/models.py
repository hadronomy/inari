from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


DATA_KEY_BYTES = 32
NONCE_BYTES = 12
FORMAT_VERSION = 1
MAX_IDENTIFIER_BYTES = 256


class ArtifactKind(StrEnum):
    ORIGINAL = "original"
    DERIVED_RASTER = "derived_raster"


@dataclass(frozen=True, slots=True)
class EncryptedArtifact:
    format_version: int
    job_id: str
    intent_id: str
    kind: ArtifactKind
    nonce: bytes
    ciphertext: bytes


@dataclass(frozen=True, slots=True)
class WrappedDataKey:
    format_version: int
    root_key_version: int
    job_id: str
    intent_id: str
    nonce: bytes
    ciphertext: bytes
