from __future__ import annotations

import json
from dataclasses import dataclass
from secrets import token_urlsafe
from threading import Lock
from typing import Protocol

import httpx
import keyring
from keyring.errors import KeyringError, NoKeyringError
from inari.local_api.schemas import (
    LocalChallengeResponse,
    LocalPairingCompleteResponse,
    LocalPairingStartResponse,
    TokenResponse,
)
from platformdirs import user_data_path

from inari.security.local_trust.crypto import (
    generate_local_client_key_pair,
    sign_local_challenge,
)
from inari.security.local_trust.models import LocalChallengePurpose
from inari.security.files import write_text_owner_only

from .config import TraySettings

TRAY_PRIVATE_KEY_NAME = "local_trust_private_key"


class TrayPairingContext:
    """Runtime-only pairing bootstrap shared by the tray bridge and API client."""

    def __init__(self) -> None:
        self._bootstrap_secret: str | None = None
        self._lock = Lock()

    def ensure_bootstrap_secret(self) -> str:
        with self._lock:
            if self._bootstrap_secret is None:
                self._bootstrap_secret = token_urlsafe(32)
            return self._bootstrap_secret

    def bootstrap_secret(self) -> str | None:
        with self._lock:
            return self._bootstrap_secret

    def clear_bootstrap_secret(self) -> None:
        with self._lock:
            self._bootstrap_secret = None


@dataclass(slots=True, frozen=True)
class TrayLocalIdentity:
    client_id: str
    client_name: str
    private_key_pem: str
    public_key_pem: str

    def sign(self, *, purpose: LocalChallengePurpose, challenge: str) -> str:
        return sign_local_challenge(
            private_key_pem=self.private_key_pem,
            purpose=purpose,
            challenge=challenge,
        )


class LocalIdentityStore(Protocol):
    def get_or_create(self) -> TrayLocalIdentity: ...


class TrayIdentityStore:
    def __init__(self, settings: TraySettings) -> None:
        self.settings = settings
        self.service_name = settings.trust_store_service_name
        self.fallback_path = settings.trust_store_path or (
            user_data_path("inari-tray", "Inari") / "local-trust.json"
        )

    def get_or_create(self) -> TrayLocalIdentity:
        existing = self._load()
        if existing is not None:
            return existing
        key_pair = generate_local_client_key_pair(prefix="tray")
        identity = TrayLocalIdentity(
            client_id=key_pair.client_id,
            client_name=self.settings.auth_client_name,
            private_key_pem=key_pair.private_key_pem,
            public_key_pem=key_pair.public_key_pem,
        )
        self._save(identity)
        return identity

    def _load(self) -> TrayLocalIdentity | None:
        payload = self._load_payload()
        if payload is None:
            return None
        try:
            return TrayLocalIdentity(
                client_id=str(payload["client_id"]),
                client_name=str(
                    payload.get("client_name") or self.settings.auth_client_name
                ),
                private_key_pem=str(payload["private_key_pem"]),
                public_key_pem=str(payload["public_key_pem"]),
            )
        except KeyError:
            return None

    def _save(self, identity: TrayLocalIdentity) -> None:
        payload = {
            "client_id": identity.client_id,
            "client_name": identity.client_name,
            "private_key_pem": identity.private_key_pem,
            "public_key_pem": identity.public_key_pem,
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True)
        self._save_fallback(encoded)
        try:
            keyring.set_password(self.service_name, TRAY_PRIVATE_KEY_NAME, encoded)
        except (KeyringError, NoKeyringError):
            return

    def _load_payload(self) -> dict[str, object] | None:
        try:
            value = keyring.get_password(self.service_name, TRAY_PRIVATE_KEY_NAME)
        except (KeyringError, NoKeyringError):
            value = None
        if value is None:
            value = self._load_fallback()
        if value is None:
            return None
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return None
        return payload

    def _load_fallback(self) -> str | None:
        if not self.fallback_path.exists():
            return None
        return self.fallback_path.read_text(encoding="utf-8")

    def _save_fallback(self, value: str) -> None:
        write_text_owner_only(self.fallback_path, value, encoding="utf-8")


class TrayLocalTrustClient:
    def __init__(
        self,
        settings: TraySettings,
        *,
        identity_store: LocalIdentityStore | None = None,
        pairing_context: TrayPairingContext | None = None,
    ) -> None:
        self.settings = settings
        self._identity_store = identity_store or TrayIdentityStore(settings)
        self._pairing_context = pairing_context or TrayPairingContext()

    def request_token(self, client: httpx.Client) -> TokenResponse:
        identity = self._identity_store.get_or_create()
        try:
            response = self._post_local_token(client, identity)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if not _is_pairing_required_error(exc.response):
                raise
            self._pair_local_identity(client, identity)
            response = self._post_local_token(client, identity)
            response.raise_for_status()
        return TokenResponse.model_validate(response.json())

    def _post_local_token(
        self, client: httpx.Client, identity: TrayLocalIdentity
    ) -> httpx.Response:
        challenge = self._issue_challenge(
            client,
            purpose=LocalChallengePurpose.TOKEN,
            client_id=identity.client_id,
        )
        return client.post(
            "/auth/local-token",
            json={
                "client_name": identity.client_name,
                "attestation": {
                    "client_id": identity.client_id,
                    "challenge_id": challenge.challenge_id,
                    "signature": identity.sign(
                        purpose=LocalChallengePurpose.TOKEN,
                        challenge=challenge.challenge,
                    ),
                },
            },
        )

    def _pair_local_identity(
        self, client: httpx.Client, identity: TrayLocalIdentity
    ) -> LocalPairingCompleteResponse:
        bootstrap_secret = self._pairing_context.bootstrap_secret()
        if bootstrap_secret is not None:
            try:
                response = self._complete_pairing(
                    client,
                    identity=identity,
                    pairing_secret=bootstrap_secret,
                )
                self._pairing_context.clear_bootstrap_secret()
                return response
            except httpx.HTTPStatusError as exc:
                if not _is_bootstrap_secret_error(exc.response):
                    raise
                self._pairing_context.clear_bootstrap_secret()

        pairing = self._start_pairing(client)
        return self._complete_pairing(
            client,
            identity=identity,
            pairing_secret=pairing.pairing_secret,
        )

    def _complete_pairing(
        self,
        client: httpx.Client,
        *,
        identity: TrayLocalIdentity,
        pairing_secret: str,
    ) -> LocalPairingCompleteResponse:
        challenge = self._issue_challenge(
            client,
            purpose=LocalChallengePurpose.PAIRING,
            client_id=identity.client_id,
        )
        response = client.post(
            "/auth/pairing/complete",
            json={
                "client_id": identity.client_id,
                "client_name": identity.client_name,
                "public_key_pem": identity.public_key_pem,
                "pairing_secret": pairing_secret,
                "attestation": {
                    "client_id": identity.client_id,
                    "challenge_id": challenge.challenge_id,
                    "signature": identity.sign(
                        purpose=LocalChallengePurpose.PAIRING,
                        challenge=challenge.challenge,
                    ),
                },
            },
        )
        response.raise_for_status()
        return LocalPairingCompleteResponse.model_validate(response.json())

    def _issue_challenge(
        self,
        client: httpx.Client,
        *,
        purpose: LocalChallengePurpose,
        client_id: str,
    ) -> LocalChallengeResponse:
        response = client.post(
            "/auth/local-challenge",
            json={"purpose": purpose.value, "client_id": client_id},
        )
        response.raise_for_status()
        return LocalChallengeResponse.model_validate(response.json())

    def _start_pairing(self, client: httpx.Client) -> LocalPairingStartResponse:
        response = client.post("/auth/pairing/start")
        response.raise_for_status()
        return LocalPairingStartResponse.model_validate(response.json())


def _is_pairing_required_error(response: httpx.Response) -> bool:
    return _error_code(response) in {
        "LOCAL_PAIRING_REQUIRED",
        "LOCAL_CLIENT_NOT_PAIRED",
    }


def _is_bootstrap_secret_error(response: httpx.Response) -> bool:
    return _error_code(response) in {
        "LOCAL_PAIRING_NOT_STARTED",
        "LOCAL_PAIRING_SECRET_INVALID",
    }


def _error_code(response: httpx.Response) -> str | None:
    if response.status_code not in {403, 409}:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    value = payload.get("code")
    return value if isinstance(value, str) else None
