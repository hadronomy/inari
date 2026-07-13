from __future__ import annotations

import pytest
from keyring.errors import NoKeyringError

from inari_tray.config import TraySettings
from inari_tray.local_trust import (
    TrayCredentialStoreUnavailable,
    TrayIdentityStore,
)


def test_installed_identity_fails_closed_without_credential_store(mocker) -> None:
    mocker.patch(
        "inari_tray.local_trust.keyring.get_password", side_effect=NoKeyringError()
    )
    store = TrayIdentityStore(TraySettings(profile="installed"))

    with pytest.raises(TrayCredentialStoreUnavailable):
        store.get_or_create()


def test_development_identity_uses_fallback_only_after_keyring_failure(
    mocker,
    tmp_path,
) -> None:
    mocker.patch("inari_tray.local_trust.keyring.get_password", return_value=None)
    mocker.patch(
        "inari_tray.local_trust.keyring.set_password", side_effect=NoKeyringError()
    )
    fallback = tmp_path / "local-trust.json"
    store = TrayIdentityStore(
        TraySettings(profile="development", trust_store_path=fallback)
    )

    identity = store.get_or_create()

    assert fallback.exists()
    assert store.get_or_create() == identity
