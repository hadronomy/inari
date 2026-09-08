from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest
from sqlalchemy import insert, select, func
from sqlalchemy.exc import IntegrityError

from inari.db import DatabaseMigrator
from inari.db.schema import devices_table, device_authority_revisions_table
from inari.device_authority import (
    AuthorityError,
    DeviceCapabilityAuthority,
    SignedAuthorityRevision,
    SignerPurpose,
    SqliteDeviceAuthorityReader,
    canonical_digest,
    canonical_json_bytes,
)
from inari.device_authority.bundle import (
    AuthorityBundle,
    AuthorityManifest,
    BindingActivation,
)
from inari.device_authority.install import DeviceAuthorityInstaller
from inari.runtime.store import RuntimeStore

from .test_device_capability_authority import NOW, _fixture


@pytest.fixture
def installation(tmp_path):
    projections, observations, target, _ = _fixture()
    now = NOW
    manifest = AuthorityManifest(
        contract="inari.device-authority.v1",
        agent_id="agent-1",
        scope=target.scope,
        signers=tuple(
            item
            for item in projections.signers.values()
            if item.purpose is not SignerPurpose.AUTHORITY_REVISION
        ),
        profiles=(projections.profile,),
        certification_rows=(projections.row,),
        bindings=(projections.binding,),
        evidence=(projections.evidence,),
        activations=(
            BindingActivation(
                revision_id=target.binding_revision_id,
                evidence_id=projections.evidence.evidence.evidence_id,
            ),
        ),
    )
    signed = projections.authority_state.current_revision
    key = projections.private_keys[signed.signer_key_id]

    def bundle_for(manifest=manifest, number=1):
        revision = replace(
            signed.revision,
            revision_id=f"authority-{number}",
            revision_number=number,
            manifest_digest=canonical_digest(manifest.model_dump(mode="json")),
        )
        return AuthorityBundle(
            revision=SignedAuthorityRevision(
                revision,
                canonical_digest(revision),
                signed.signer_key_id,
                key.sign(canonical_json_bytes(revision)),
            ),
            manifest=manifest,
        )

    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    store = RuntimeStore(path)
    with store.connection() as connection:
        connection.execute(
            insert(devices_table).values(
                id=target.device_id,
                kind="printer",
                driver_key="driver-escpos",
                identity_transport="spooler",
                name="Test fixture",
                connection_state="ready",
                first_seen_at=now.isoformat(),
                last_seen_at=now.isoformat(),
                updated_at=now.isoformat(),
                is_default=False,
                capabilities_json="{}",
                metadata_json="{}",
            )
        )
    installer = DeviceAuthorityInstaller(
        store,
        trusted_signer=projections.signers[signed.signer_key_id],
        agent_id="agent-1",
        scope=target.scope,
    )
    yield installer, store, bundle_for, observations, target, now
    store.engine.dispose()


def test_installed_bundle_authorizes_through_sqlite(installation):
    installer, store, bundle_for, observations, target, now = installation
    bundle = AuthorityBundle.model_validate_json(bundle_for().model_dump_json())
    assert installer.install(bundle, now=now)
    reader = SqliteDeviceAuthorityReader(store)
    authority = DeviceCapabilityAuthority(
        projections=reader, observations=observations, current_agent_version="1.20.0"
    )
    permit = authority.authorize(target, now=now)
    assert (
        authority.check(permit, now=now).binding_revision_id
        == target.binding_revision_id
    )
    assert not installer.install(bundle, now=now)


def test_rejects_manifest_tampering_without_writes(installation):
    installer, store, bundle_for, _, _, now = installation
    bundle = bundle_for()
    tampered = bundle.model_copy(
        update={"manifest": bundle.manifest.model_copy(update={"activations": ()})}
    )
    with pytest.raises(ValueError, match="manifest digest"):
        installer.install(tampered, now=now)
    assert SqliteDeviceAuthorityReader(store).read_authority_state() is None


@pytest.mark.parametrize(
    "field,value", [("agent_id", "another-agent"), ("contract", "unsupported")]
)
def test_rejects_wrong_target_or_contract(installation, field, value):
    installer, _, bundle_for, _, _, now = installation
    raw = bundle_for().model_dump(mode="json")
    raw["manifest"][field] = value
    with pytest.raises(ValueError):
        installer.install(AuthorityBundle.model_validate_json(json.dumps(raw)), now=now)


def test_rejects_nested_unknown_fields(installation):
    _, _, bundle_for, _, _, _ = installation
    raw = (
        bundle_for().model_dump_json().replace('"scope":{', '"scope":{"ignored":true,')
    )
    with pytest.raises(ValueError):
        AuthorityBundle.model_validate_json(raw)


def test_rejects_rollback_and_revision_replacement(installation):
    installer, store, bundle_for, _, _, now = installation
    installer.install(bundle_for(number=2), now=now)
    with pytest.raises(ValueError, match="backwards"):
        installer.install(bundle_for(number=1), now=now)
    empty = bundle_for().manifest.model_copy(update={"activations": ()})
    with pytest.raises(ValueError, match="changed in place"):
        installer.install(bundle_for(empty, number=2), now=now)
    assert (
        SqliteDeviceAuthorityReader(store)
        .read_authority_state()
        .current_revision.revision.revision_number
        == 2
    )


def test_new_snapshot_deactivates_omitted_binding(installation):
    installer, store, bundle_for, observations, target, now = installation
    installer.install(bundle_for(), now=now)
    reader = SqliteDeviceAuthorityReader(store)
    authority = DeviceCapabilityAuthority(
        projections=reader, observations=observations, current_agent_version="1.20.0"
    )
    permit = authority.authorize(target, now=now)
    empty = bundle_for().manifest.model_copy(update={"activations": ()})
    installer.install(bundle_for(empty, number=2), now=now)
    with pytest.raises(AuthorityError):
        authority.check(permit, now=now)


def test_missing_device_rolls_back_all_projection_writes(installation):
    installer, store, bundle_for, _, _, now = installation
    with store.connection() as connection:
        connection.execute(devices_table.delete())
    with pytest.raises(IntegrityError):
        installer.install(bundle_for(), now=now)
    with store.connection() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(device_authority_revisions_table)
            )
            == 0
        )


def test_expired_bundle_cannot_install(installation):
    installer, store, bundle_for, _, _, now = installation
    with pytest.raises(ValueError, match="bounded lifetime"):
        installer.install(bundle_for(), now=now + timedelta(days=8))
    assert SqliteDeviceAuthorityReader(store).read_authority_state() is None


def test_invalid_record_signature_cannot_install(installation):
    installer, store, bundle_for, _, _, now = installation
    manifest = bundle_for().manifest
    broken = replace(manifest.profiles[0], signature=b"x" * 64)
    bundle = bundle_for(manifest.model_copy(update={"profiles": (broken,)}))
    with pytest.raises(AuthorityError):
        installer.install(bundle, now=now)
    assert SqliteDeviceAuthorityReader(store).read_authority_state() is None


def test_purpose_mismatch_cannot_install(installation):
    installer, store, bundle_for, _, _, now = installation
    manifest = bundle_for().manifest
    signers = tuple(
        replace(item, purpose=SignerPurpose.DEVICE_OBSERVATION)
        if item.purpose is SignerPurpose.DRIVER_PROFILE
        else item
        for item in manifest.signers
    )
    with pytest.raises(AuthorityError):
        installer.install(
            bundle_for(manifest.model_copy(update={"signers": signers})), now=now
        )
    assert SqliteDeviceAuthorityReader(store).read_authority_state() is None


def test_install_preserves_quarantine(installation):
    from inari.db.schema import device_authority_state_table

    installer, store, bundle_for, _, _, now = installation
    installer.install(bundle_for(), now=now)
    with store.connection() as connection:
        connection.execute(
            device_authority_state_table.update().values(
                status="quarantined",
                quarantined_at=now.isoformat(),
                quarantine_reason_code="test_quarantine",
            )
        )
    with pytest.raises(ValueError, match="Quarantine"):
        installer.install(bundle_for(number=2), now=now)
    assert (
        SqliteDeviceAuthorityReader(store).read_authority_state().status.value
        == "quarantined"
    )


def test_cli_installs_empty_signed_authority_for_existing_agent(tmp_path):
    from datetime import UTC, datetime
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from typer.testing import CliRunner

    from inari.cli import app
    from inari.config import load_settings
    from inari.device_authority.models import (
        AuthorityRevision,
        SignerRecord,
        SignerState,
    )
    from inari.device_authority.bundle import AuthorityTrust
    from inari.security.identity import AgentIdentityService

    now = datetime.now(UTC)
    config = tmp_path / "inari.toml"
    config.write_text('[storage]\nprofile = "development"\n')
    settings = load_settings(config_path=config)
    identity = AgentIdentityService(
        identity_path=settings.resolved_security_state_dir / "agent-identity.pem"
    ).get_or_create_identity()
    _, _, target, _ = _fixture()
    private = Ed25519PrivateKey.generate()
    signer = SignerRecord(
        "controller-authority",
        SignerPurpose.AUTHORITY_REVISION,
        private.public_key().public_bytes_raw(),
        SignerState.ACTIVE,
        now - timedelta(days=1),
        now + timedelta(days=30),
        None,
    )
    manifest = AuthorityManifest(
        contract="inari.device-authority.v1",
        agent_id=identity.agent_id,
        scope=target.scope,
        signers=(),
        profiles=(),
        certification_rows=(),
        bindings=(),
        evidence=(),
        activations=(),
    )
    revision = AuthorityRevision(
        "authority-1",
        1,
        canonical_digest(manifest.model_dump(mode="json")),
        now - timedelta(minutes=1),
        now + timedelta(days=1),
    )
    bundle = AuthorityBundle(
        revision=SignedAuthorityRevision(
            revision,
            canonical_digest(revision),
            signer.key_id,
            private.sign(canonical_json_bytes(revision)),
        ),
        manifest=manifest,
    )
    bundle_path = tmp_path / "authority.json"
    bundle_path.write_text(bundle.model_dump_json())
    trust_path = tmp_path / "trust.json"
    trust_path.write_text(
        AuthorityTrust(scope=target.scope, signer=signer).model_dump_json()
    )
    result = CliRunner().invoke(
        app,
        [
            "authority",
            "install",
            "--config",
            str(config),
            "--bundle",
            str(bundle_path),
            "--trust",
            str(trust_path),
        ],
    )
    assert result.exit_code == 0, repr(result.exception)
    assert "authority-1: installed" in result.output
    store = RuntimeStore(settings.resolved_runtime_database_path)
    try:
        assert (
            SqliteDeviceAuthorityReader(store)
            .read_authority_state()
            .current_revision.digest
            == bundle.revision.digest
        )
    finally:
        store.engine.dispose()
