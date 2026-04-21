from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ...core.exceptions import AgentError
from .models import LocalChallengePurpose


@dataclass(slots=True, frozen=True)
class LocalClientKeyPair:
    private_key_pem: str
    public_key_pem: str
    client_id: str


def generate_local_client_key_pair(*, prefix: str = "client") -> LocalClientKeyPair:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_pem = _public_key_to_pem(public_key)
    fingerprint = public_key_fingerprint(public_key_pem)
    return LocalClientKeyPair(
        private_key_pem=_private_key_to_pem(private_key),
        public_key_pem=public_key_pem,
        client_id=f"{prefix}_{fingerprint[:24]}",
    )


def sign_local_challenge(
    *,
    private_key_pem: str,
    purpose: LocalChallengePurpose,
    challenge: str,
) -> str:
    private_key = _load_private_key(private_key_pem)
    signature = private_key.sign(_challenge_message(purpose, challenge))
    return _urlsafe_b64encode(signature)


def verify_local_challenge_signature(
    *,
    public_key_pem: str,
    purpose: LocalChallengePurpose,
    challenge: str,
    signature: str,
) -> None:
    public_key = _load_public_key(public_key_pem)
    try:
        public_key.verify(
            _urlsafe_b64decode(signature),
            _challenge_message(purpose, challenge),
        )
    except InvalidSignature as exc:
        raise AgentError(
            "LOCAL_CLIENT_ATTESTATION_FAILED",
            "The local client signature did not match the issued challenge.",
            status_code=403,
        ) from exc


def public_key_fingerprint(public_key_pem: str) -> str:
    public_key = _load_public_key(public_key_pem)
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _private_key_to_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _public_key_to_pem(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def _load_private_key(private_key_pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), None)
    if not isinstance(key, Ed25519PrivateKey):
        raise AgentError(
            "LOCAL_CLIENT_KEY_INVALID",
            "The local client private key must be an Ed25519 key.",
            status_code=400,
        )
    return key


def _load_public_key(public_key_pem: str) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise AgentError(
            "LOCAL_CLIENT_KEY_INVALID",
            "The local client public key must be an Ed25519 key.",
            status_code=400,
        )
    return key


def _challenge_message(purpose: LocalChallengePurpose, challenge: str) -> bytes:
    return f"inari.local-trust.v1:{purpose.value}:{challenge}".encode("utf-8")


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
