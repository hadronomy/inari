from __future__ import annotations

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
from pydantic import BaseModel, ConfigDict, ValidationError

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


class PairingBootstrap(Protocol):
    def start(self, client: httpx.Client) -> LocalPairingStartResponse: ...


class HttpPairingBootstrap:
    def start(self, client: httpx.Client) -> LocalPairingStartResponse:
        response = client.post("/auth/pairing/start")
        response.raise_for_status()
        return LocalPairingStartResponse.model_validate(response.json())


class NativePairingBootstrap:
    def start(self, client: httpx.Client) -> LocalPairingStartResponse:
        del client
        from .windows_pairing import WindowsPairingBootstrapClient

        response = WindowsPairingBootstrapClient().request()
        return LocalPairingStartResponse(
            pairing_secret=response.pairing_secret,
            expires_at=response.expires_at,
        )


class TrayIdentityStore:
    def __init__(self, settings: TraySettings) -> None:
        self.settings = settings
        self.service_name = settings.trust_store_service_name
        self.fallback_path = (
            None
            if settings.profile == "installed"
            else settings.trust_store_path
            or (user_data_path("inari-tray", "Inari") / "local-trust.json")
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
        encoded = self._load_encoded()
        if encoded is None:
            return None
        try:
            payload = StoredTrayIdentity.model_validate_json(encoded)
            return TrayLocalIdentity(
                client_id=payload.client_id,
                client_name=payload.client_name,
                private_key_pem=payload.private_key_pem,
                public_key_pem=payload.public_key_pem,
            )
        except ValidationError:
            return None

    def _save(self, identity: TrayLocalIdentity) -> None:
        encoded = StoredTrayIdentity(
            client_id=identity.client_id,
            client_name=identity.client_name,
            private_key_pem=identity.private_key_pem,
            public_key_pem=identity.public_key_pem,
        ).model_dump_json(indent=2)
        try:
            keyring.set_password(self.service_name, TRAY_PRIVATE_KEY_NAME, encoded)
        except (KeyringError, NoKeyringError) as exc:
            if self.fallback_path is None:
                raise TrayCredentialStoreUnavailable(
                    "The Windows credential store is unavailable."
                ) from exc
            self._save_fallback(encoded)
            return
        if self.fallback_path is not None:
            self.fallback_path.unlink(missing_ok=True)

    def _load_encoded(self) -> str | None:
        try:
            value = keyring.get_password(self.service_name, TRAY_PRIVATE_KEY_NAME)
        except (KeyringError, NoKeyringError) as exc:
            if self.fallback_path is None:
                raise TrayCredentialStoreUnavailable(
                    "The Windows credential store is unavailable."
                ) from exc
            return self._load_fallback()
        if value is not None or self.fallback_path is None:
            return value
        legacy = self._load_fallback()
        if legacy is None:
            return None
        try:
            keyring.set_password(self.service_name, TRAY_PRIVATE_KEY_NAME, legacy)
        except (KeyringError, NoKeyringError):
            return legacy
        self.fallback_path.unlink(missing_ok=True)
        return legacy

    def _load_fallback(self) -> str | None:
        if self.fallback_path is None or not self.fallback_path.exists():
            return None
        return self.fallback_path.read_text(encoding="utf-8")

    def _save_fallback(self, value: str) -> None:
        if self.fallback_path is None:
            raise TrayCredentialStoreUnavailable(
                "Plaintext credential fallback is disabled for installed Device Center."
            )
        write_text_owner_only(self.fallback_path, value, encoding="utf-8")


class TrayCredentialStoreUnavailable(RuntimeError):
    """Raised when installed Device Center cannot use protected credentials."""


class StoredTrayIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str
    client_name: str
    private_key_pem: str
    public_key_pem: str


class TrayLocalTrustClient:
    def __init__(
        self,
        settings: TraySettings,
        *,
        identity_store: LocalIdentityStore | None = None,
        pairing_context: TrayPairingContext | None = None,
        pairing_bootstrap: PairingBootstrap | None = None,
    ) -> None:
        self.settings = settings
        self._identity_store = identity_store or TrayIdentityStore(settings)
        self._pairing_context = pairing_context or TrayPairingContext()
        self._pairing_bootstrap = pairing_bootstrap or (
            NativePairingBootstrap()
            if settings.profile == "installed"
            else HttpPairingBootstrap()
        )

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
        return self._pairing_bootstrap.start(client)


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
