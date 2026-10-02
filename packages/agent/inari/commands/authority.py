from __future__ import annotations

from pathlib import Path
import json

import typer

from ..config import load_settings
from ..db import DatabaseMigrator
from ..device_authority.bundle import AuthorityBundle, AuthorityTrust
from ..device_authority.install import DeviceAuthorityInstaller
from ..runtime.store import RuntimeStore
from ..security.identity import AgentIdentityService
from ..device_authority.observations import DeviceObservationSigningKey
from ..di.security import build_secret_store


def run_observation_key(config_path: Path | None) -> None:
    """Print the Agent public key for a Controller-signed observation signer."""
    settings = load_settings(config_path=config_path)
    key = DeviceObservationSigningKey(build_secret_store(settings))
    typer.echo(
        json.dumps(
            {"key_id": key.key_id(), "public_key": key.public_key().hex()},
            sort_keys=True,
        )
    )


def run_install(config_path: Path | None, bundle_path: Path, trust_path: Path) -> None:
    """Import signed authority using trust provisioned by the Agent Administrator."""
    settings = load_settings(config_path=config_path)
    trust = AuthorityTrust.model_validate_json(_read_bounded(trust_path, 16 * 1024))
    bundle = AuthorityBundle.model_validate_json(
        _read_bounded(bundle_path, 8 * 1024 * 1024)
    )
    identity_path = settings.resolved_security_state_dir / "agent-identity.pem"
    if not identity_path.is_file():
        raise ValueError(
            "Start the Agent once to establish its identity before importing authority."
        )
    identity = AgentIdentityService(
        identity_path=identity_path
    ).get_or_create_identity()
    DatabaseMigrator(settings.resolved_runtime_database_path).ensure_current()
    store = RuntimeStore(settings.resolved_runtime_database_path)
    try:
        changed = DeviceAuthorityInstaller(
            store,
            trusted_signer=trust.signer,
            agent_id=identity.agent_id,
            scope=trust.scope,
        ).install(bundle)
    finally:
        store.engine.dispose()
    revision = bundle.revision.revision.revision_id
    typer.echo(
        f"Authority revision {revision}: {'installed' if changed else 'already installed'}."
    )


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError(f"Authority input exceeds {limit} bytes.")
    return value
