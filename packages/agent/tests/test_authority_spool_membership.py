from dataclasses import replace
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from inari.db.schema import device_binding_revisions_table
from inari.db import DatabaseMigrator
from inari.core.failures import ProblemCode
from inari.device_authority import (
    DeviceCapabilityAuthority,
    SqliteDeviceAuthorityReader,
)
from inari.spool.authority import SqlActiveAuthorityGuard
from inari.spool.ledger import SpoolAdmissionLedger
from inari.spool.limits import SpoolCapacityPolicy
from inari.spool.manifest import manifest_from_admission
from inari.spool.owner import SpoolOwner
from inari.spool.errors import SpoolAdmissionError
from pathlib import Path

from .test_device_authority_install import installation as installation
from .test_durable_spool_admission import _admission, _jpeg_like_work


def _prepare(store, target, proof, now):
    manifest = replace(
        manifest_from_admission(_admission(_jpeg_like_work())),
        database=target.scope.database,
        device_id=target.device_id,
        binding_revision_id=target.binding_revision_id,
        deadline_at=(now + timedelta(minutes=5))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        normalized_options_digest=bytes.fromhex(proof.options_digest),
        authority_proof=proof,
    )
    ids = iter(("admission-1", "job-1", "reservation-1", "artifact-1"))
    ledger = SpoolAdmissionLedger(
        store, SpoolOwner("test", 1), SpoolCapacityPolicy(), lambda: next(ids)
    )
    return ledger.prepare(manifest, now=now)


def test_receipt_proof_accepts_records_reused_by_a_new_bundle(installation):
    installer, store, bundle_for, observations, target, now = installation
    first = bundle_for()
    installer.install(first, now=now)
    second = bundle_for(number=2)
    installer.install(second, now=now)
    authority = DeviceCapabilityAuthority(
        projections=SqliteDeviceAuthorityReader(store),
        observations=observations,
        current_agent_version="1.20.0",
    )
    proof = authority.check(authority.authorize(target, now=now), now=now)
    assert proof.authority_revision_id == second.revision.revision.revision_id
    with store.connection() as connection:
        assert (
            connection.execute(
                select(device_binding_revisions_table.c.authority_revision_id)
            ).scalar_one()
            == first.revision.revision.revision_id
        )
    migrator = DatabaseMigrator(Path(store.engine.url.database))
    command.downgrade(migrator._build_alembic_config(), "20261006_0018")
    with pytest.raises(IntegrityError, match="authority proof graph is invalid"):
        _prepare(store, target, proof, now)
    upgraded = migrator.ensure_current()
    assert upgraded.previous_revision == "20261006_0018"
    assert upgraded.backup_path is not None
    _prepare(store, target, proof, now)
    with store.immediate_transaction() as connection:
        SqlActiveAuthorityGuard().check(connection, proof, now=now)


def test_retained_binding_uses_the_signed_bundle_expiry(installation):
    installer, store, bundle_for, observations, target, now = installation
    installer.install(bundle_for(expires_at=now + timedelta(seconds=10)), now=now)
    installer.install(
        bundle_for(number=2, expires_at=now + timedelta(minutes=5)), now=now
    )
    authority = DeviceCapabilityAuthority(
        projections=SqliteDeviceAuthorityReader(store),
        observations=observations,
        current_agent_version="1.20.0",
    )
    proof = authority.check(authority.authorize(target, now=now), now=now)
    _prepare(store, target, proof, now)
    with store.immediate_transaction() as connection:
        SqlActiveAuthorityGuard().check(
            connection, proof, now=now + timedelta(seconds=20)
        )


@pytest.mark.parametrize(
    "withdrawn", ["bindings", "profiles", "certification_rows", "evidence"]
)
def test_database_rejects_proof_for_a_record_absent_from_its_bundle(
    installation, withdrawn
):
    installer, store, bundle_for, observations, target, now = installation
    installer.install(bundle_for(), now=now)
    authority = DeviceCapabilityAuthority(
        projections=SqliteDeviceAuthorityReader(store),
        observations=observations,
        current_agent_version="1.20.0",
    )
    proof = authority.check(authority.authorize(target, now=now), now=now)
    second = bundle_for(
        bundle_for().manifest.model_copy(update={withdrawn: (), "activations": ()}),
        number=2,
    )
    installer.install(second, now=now)
    changed = replace(
        proof,
        authority_revision_id=second.revision.revision.revision_id,
        authority_revision_number=2,
        authority_revision_digest=second.revision.digest,
    )
    with pytest.raises(IntegrityError, match="authority proof graph is invalid"):
        _prepare(store, target, changed, now)


@pytest.mark.parametrize(
    "withdrawn", ["bindings", "profiles", "certification_rows", "evidence"]
)
def test_publication_rejects_a_graph_withdrawn_by_the_next_bundle(
    installation, withdrawn
):
    installer, store, bundle_for, observations, target, now = installation
    installer.install(bundle_for(), now=now)
    authority = DeviceCapabilityAuthority(
        projections=SqliteDeviceAuthorityReader(store),
        observations=observations,
        current_agent_version="1.20.0",
    )
    proof = authority.check(authority.authorize(target, now=now), now=now)
    _prepare(store, target, proof, now)
    installer.install(
        bundle_for(
            bundle_for().manifest.model_copy(update={withdrawn: (), "activations": ()}),
            number=2,
        ),
        now=now,
    )
    with (
        pytest.raises(SpoolAdmissionError) as error,
        store.immediate_transaction() as connection,
    ):
        SqlActiveAuthorityGuard().check(connection, proof, now=now)
    assert error.value.code is ProblemCode.CAPABILITY_CHANGED
