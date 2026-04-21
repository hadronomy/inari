from __future__ import annotations

from .models import LocalPairingSecret, LocalTrustState, TrustedLocalClient
from ..secrets import SecretStore

LOCAL_TRUST_STATE_KEY = "standalone_local_trust_state"


class LocalTrustStore:
    def __init__(self, secret_store: SecretStore) -> None:
        self.secret_store = secret_store

    def load(self) -> LocalTrustState:
        raw_value = self.secret_store.get_secret(LOCAL_TRUST_STATE_KEY)
        if raw_value is None:
            return LocalTrustState()
        return LocalTrustState.model_validate_json(raw_value)

    def save(self, state: LocalTrustState) -> None:
        self.secret_store.set_secret(
            LOCAL_TRUST_STATE_KEY,
            state.model_dump_json(exclude_none=True),
        )

    def set_pairing_secret(self, pairing_secret: LocalPairingSecret) -> LocalTrustState:
        state = self.load().model_copy(update={"pairing_secret": pairing_secret})
        self.save(state)
        return state

    def clear_pairing_secret(self) -> LocalTrustState:
        state = self.load().model_copy(update={"pairing_secret": None})
        self.save(state)
        return state

    def upsert_client(self, client: TrustedLocalClient) -> LocalTrustState:
        state = self.load()
        clients = [
            existing
            for existing in state.trusted_clients
            if existing.client_id != client.client_id
        ]
        clients.append(client)
        state = state.model_copy(
            update={"trusted_clients": clients, "pairing_secret": None}
        )
        self.save(state)
        return state

    def touch_client(self, client: TrustedLocalClient) -> LocalTrustState:
        state = self.load()
        clients = [
            client if existing.client_id == client.client_id else existing
            for existing in state.trusted_clients
        ]
        state = state.model_copy(update={"trusted_clients": clients})
        self.save(state)
        return state

    def revoke_client(self, client_id: str) -> LocalTrustState:
        state = self.load()
        state = state.model_copy(
            update={
                "trusted_clients": [
                    client
                    for client in state.trusted_clients
                    if client.client_id != client_id
                ]
            }
        )
        self.save(state)
        return state
