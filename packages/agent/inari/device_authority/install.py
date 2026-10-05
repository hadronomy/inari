from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Table, insert, select, update
from sqlalchemy.engine import Connection

from ..db.schema import (
    device_authority_revisions_table,
    device_authority_signer_keys_table,
    device_authority_state_table,
    device_binding_authority_state_table,
    device_binding_revisions_table,
    device_driver_profiles_table,
    device_test_evidence_table,
    hardware_certification_matrix_rows_table,
)
from ..runtime.store import RuntimeStore
from .authority import (
    authority_revision_payload,
    binding_revision_payload,
    certification_row_payload,
    canonical_json_bytes,
    device_test_payload,
    driver_profile_payload,
)
from .bundle import AuthorityBundle
from .models import AuthorityScope, SignerRecord


class DeviceAuthorityInstaller:
    """Install verified Controller authority in one durable transaction."""

    def __init__(
        self,
        store: RuntimeStore,
        *,
        trusted_signer: SignerRecord,
        agent_id: str,
        scope: AuthorityScope,
    ) -> None:
        self._store = store
        self._trusted_signer = trusted_signer
        self._agent_id = agent_id
        self._scope = scope

    def install(self, bundle: AuthorityBundle, *, now: datetime | None = None) -> bool:
        at = now or datetime.now(UTC)
        bundle.verify(
            trusted_signer=self._trusted_signer,
            agent_id=self._agent_id,
            scope=self._scope,
            now=at,
        )
        with self._store.immediate_transaction() as connection:
            current = (
                connection.execute(select(device_authority_state_table))
                .mappings()
                .one_or_none()
            )
            if current is not None:
                if current["status"] == "quarantined":
                    raise ValueError(
                        "Authority installation cannot clear Agent Quarantine."
                    )
                number = bundle.revision.revision.revision_number
                if number < current["current_revision_number"]:
                    raise ValueError("The authority revision moved backwards.")
                if number == current["current_revision_number"]:
                    existing = (
                        connection.execute(
                            select(device_authority_revisions_table).where(
                                device_authority_revisions_table.c.revision_id
                                == current["current_revision_id"]
                            )
                        )
                        .mappings()
                        .one()
                    )
                    if (
                        existing["revision_id"] != bundle.revision.revision.revision_id
                        or bytes(existing["revision_digest"]).hex()
                        != bundle.revision.digest
                        or existing["signer_key_id"] != bundle.revision.signer_key_id
                        or bytes(existing["signature"]) != bundle.revision.signature
                    ):
                        raise ValueError("The authority revision changed in place.")
                    return False
            for signer in (self._trusted_signer, *bundle.manifest.signers):
                _install_signer(connection, _signer_row(signer))
            revision = bundle.revision
            revision_row = _signed_row(
                authority_revision_payload(revision.revision),
                revision,
                "revision_digest",
            )
            revision_row["manifest"] = canonical_json_bytes(
                bundle.manifest.model_dump(mode="json")
            ).decode()
            _insert_immutable(
                connection,
                device_authority_revisions_table,
                revision_row,
            )
            revision_id = revision.revision.revision_id
            for signed in bundle.manifest.profiles:
                row = driver_profile_payload(signed.profile)
                row["capabilities"] = json.dumps(
                    row["capabilities"], separators=(",", ":"), sort_keys=True
                )
                _insert_projection(
                    connection,
                    device_driver_profiles_table,
                    row,
                    signed,
                    "profile_digest",
                    revision_id,
                )
            for signed in bundle.manifest.certification_rows:
                _insert_projection(
                    connection,
                    hardware_certification_matrix_rows_table,
                    certification_row_payload(signed.row),
                    signed,
                    "matrix_row_digest",
                    revision_id,
                )
            for signed in bundle.manifest.bindings:
                row = binding_revision_payload(signed.revision)
                scope = row.pop("scope")
                row.update(scope)
                row["scope_kind"] = row.pop("kind")
                row["device_purpose"] = row.pop("purpose")
                row["effective_at"] = _timestamp(revision.revision.effective_at)
                row["expires_at"] = _timestamp(revision.revision.expires_at)
                _insert_projection(
                    connection,
                    device_binding_revisions_table,
                    row,
                    signed,
                    "binding_digest",
                    revision_id,
                )
            for signed in bundle.manifest.evidence:
                _insert_projection(
                    connection,
                    device_test_evidence_table,
                    device_test_payload(signed.evidence),
                    signed,
                    "evidence_digest",
                    revision_id,
                )
            self._activate(connection, bundle, at)
            values = dict(
                singleton_id=1,
                current_revision_id=revision_id,
                current_revision_number=revision.revision.revision_number,
                status="ready",
                quarantined_at=None,
                quarantine_reason_code=None,
                updated_at=_timestamp(at),
            )
            if current is None:
                connection.execute(
                    insert(device_authority_state_table).values(**values)
                )
            else:
                connection.execute(
                    update(device_authority_state_table)
                    .where(device_authority_state_table.c.singleton_id == 1)
                    .values(**values)
                )
        return True

    def _activate(
        self, connection: Connection, bundle: AuthorityBundle, at: datetime
    ) -> None:
        table = device_binding_authority_state_table
        scope = self._scope
        connection.execute(
            update(table)
            .where(
                table.c.database == scope.database,
                table.c.organization_id == scope.organization_id,
                table.c.site_id == scope.site_id,
                table.c.scope_kind == scope.kind.value,
                table.c.pos_configuration_id == scope.pos_configuration_id,
            )
            .values(status="inactive", updated_at=_timestamp(at))
        )
        bindings = {
            item.revision.revision_id: item.revision
            for item in bundle.manifest.bindings
        }
        for activation in bundle.manifest.activations:
            binding = bindings[activation.revision_id]
            values = dict(
                state_id=binding.binding_id,
                active_revision_id=binding.revision_id,
                active_test_evidence_id=activation.evidence_id,
                database=scope.database,
                organization_id=scope.organization_id,
                site_id=scope.site_id,
                scope_kind=scope.kind.value,
                pos_configuration_id=scope.pos_configuration_id,
                device_purpose=binding.purpose,
                status="active",
                updated_at=_timestamp(at),
            )
            old = (
                connection.execute(
                    select(table).where(table.c.state_id == binding.binding_id)
                )
                .mappings()
                .one_or_none()
            )
            if old is None:
                connection.execute(insert(table).values(**values))
            else:
                for key in (
                    "database",
                    "organization_id",
                    "site_id",
                    "scope_kind",
                    "pos_configuration_id",
                    "device_purpose",
                ):
                    if old[key] != values[key]:
                        raise ValueError(
                            "A Device Binding cannot move between scopes or purposes."
                        )
                connection.execute(
                    update(table)
                    .where(table.c.state_id == binding.binding_id)
                    .values(**values)
                )


def _timestamp(value: datetime | None) -> str | None:
    return (
        None
        if value is None
        else value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _signer_row(signer: SignerRecord) -> dict[str, Any]:
    row = asdict(signer)
    row["purpose"] = signer.purpose.value
    row["state"] = signer.state.value
    for field in ("not_before", "not_after", "retired_at"):
        row[field] = _timestamp(row[field])
    return row


def _signed_row(
    payload: dict[str, Any], signed: Any, digest_column: str
) -> dict[str, Any]:
    row = dict(payload)
    for name, value in row.items():
        if name.endswith("_digest"):
            row[name] = bytes.fromhex(value)
    row.update(
        {
            digest_column: bytes.fromhex(signed.digest),
            "signer_key_id": signed.signer_key_id,
            "signature": signed.signature,
        }
    )
    return row


def _insert_projection(
    connection: Connection,
    table: Table,
    payload: dict[str, Any],
    signed: Any,
    digest_column: str,
    revision_id: str,
) -> None:
    row = _signed_row(payload, signed, digest_column)
    row["authority_revision_id"] = revision_id
    _insert_immutable(connection, table, row, digest_column=digest_column)


def _insert_immutable(
    connection: Connection,
    table: Table,
    values: dict[str, Any],
    *,
    digest_column: str | None = None,
) -> None:
    key = next(iter(table.primary_key.columns))
    existing = (
        connection.execute(select(table).where(key == values[key.name]))
        .mappings()
        .one_or_none()
    )
    if existing is None:
        connection.execute(insert(table).values(**values))
        return
    # Reused projections keep the revision that first installed their signed body.
    compared = (
        (digest_column, "signer_key_id", "signature")
        if digest_column
        else tuple(values)
    )
    if any(existing[name] != values[name] for name in compared):
        raise ValueError(f"An immutable {table.name} record changed in place.")


def _install_signer(connection: Connection, values: dict[str, Any]) -> None:
    table = device_authority_signer_keys_table
    existing = (
        connection.execute(select(table).where(table.c.key_id == values["key_id"]))
        .mappings()
        .one_or_none()
    )
    if existing is None:
        connection.execute(insert(table).values(**values))
        return
    if any(
        existing[name] != values[name]
        for name in ("key_id", "purpose", "public_key", "not_before", "not_after")
    ):
        raise ValueError("A Device authority signer changed its identity in place.")
    if (
        existing["state"] == values["state"]
        and existing["retired_at"] == values["retired_at"]
    ):
        return
    if (
        existing["state"] != "active"
        or values["state"] != "retired"
        or existing["retired_at"] is not None
        or values["retired_at"] is None
    ):
        raise ValueError("A Device authority signer changed outside retirement.")
    connection.execute(
        update(table)
        .where(table.c.key_id == values["key_id"])
        .values(state="retired", retired_at=values["retired_at"])
    )
