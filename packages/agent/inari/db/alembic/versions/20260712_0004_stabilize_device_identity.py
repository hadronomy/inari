"""Derive device ids from stable transport identities.

Revision ID: 20260712_0004
Revises: 20260418_0003
Create Date: 2026-07-12 20:00:00
"""

from __future__ import annotations

from hashlib import sha1
import json
from typing import Any
from uuid import UUID, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping


revision = "20260712_0004"
down_revision = "20260418_0003"
branch_labels = None
depends_on = None

_DEVICE_NAMESPACE = UUID("efdbfb52-14ac-5c5c-a01c-b2a846f71d76")


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.add_column(sa.Column("identity_transport", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("identity_serial_number", sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("identity_vendor_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("identity_product_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("identity_os_instance_id", sa.String(), nullable=True)
        )
        batch_op.add_column(sa.Column("identity_port_id", sa.String(), nullable=True))

    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
    devices = connection.execute(
        sa.text(
            "SELECT id, kind, driver_key, name, metadata_json FROM devices ORDER BY id"
        )
    ).mappings()
    for device in devices:
        identity = _legacy_identity(device)
        stable_id = _stable_id(
            kind=str(device["kind"]),
            driver_key=str(device["driver_key"]),
            identity=identity,
        )
        _replace_device_id(connection, str(device["id"]), stable_id)
        connection.execute(
            sa.text(
                """
                UPDATE devices
                SET identity_transport = :transport,
                    identity_serial_number = :serial_number,
                    identity_vendor_id = :vendor_id,
                    identity_product_id = :product_id,
                    identity_os_instance_id = :os_instance_id,
                    identity_port_id = :port_id
                WHERE id = :device_id
                """
            ),
            {"device_id": stable_id, **identity},
        )

    with op.batch_alter_table("devices") as batch_op:
        batch_op.alter_column("identity_transport", nullable=False)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
    devices = connection.execute(
        sa.text("SELECT id, kind, driver_key, name FROM devices ORDER BY id")
    ).mappings()
    for device in devices:
        legacy_id = _legacy_id(
            kind=str(device["kind"]),
            driver_key=str(device["driver_key"]),
            name=str(device["name"]),
        )
        _replace_device_id(connection, str(device["id"]), legacy_id)

    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_column("identity_port_id")
        batch_op.drop_column("identity_os_instance_id")
        batch_op.drop_column("identity_product_id")
        batch_op.drop_column("identity_vendor_id")
        batch_op.drop_column("identity_serial_number")
        batch_op.drop_column("identity_transport")


def _legacy_identity(device: RowMapping) -> dict[str, Any]:
    metadata = json.loads(str(device["metadata_json"]) or "{}")
    source = str(metadata.get("source", ""))
    name = str(device["name"])
    if source == "network_config" and metadata.get("host"):
        transport = "network"
        instance = f"tcp://{metadata['host']}:{metadata.get('port', 9100)}"
    elif source == "cups":
        transport = "spooler"
        instance = str(metadata.get("device_uri") or f"cups-queue:{name}")
    elif source == "windows_spooler":
        transport = "spooler"
        instance = f"windows-queue:{metadata.get('queue_name', name)}"
    else:
        transport = "spooler"
        instance = f"legacy:{device['driver_key']}:{name}"
    return {
        "transport": transport,
        "serial_number": None,
        "vendor_id": None,
        "product_id": None,
        "os_instance_id": instance,
        "port_id": None,
    }


def _stable_id(*, kind: str, driver_key: str, identity: dict[str, Any]) -> str:
    serial_number = identity["serial_number"]
    if serial_number:
        vendor = _hex_identifier(identity["vendor_id"])
        product = _hex_identifier(identity["product_id"])
        identity_key = f"hardware:{vendor}:{product}:{serial_number}"
    elif identity["os_instance_id"]:
        identity_key = f"os:{identity['transport']}:{identity['os_instance_id']}"
    else:
        identity_key = f"port:{identity['transport']}:{identity['port_id']}"
    identity_source = "\0".join((kind, driver_key, identity_key))
    return f"dev_{uuid5(_DEVICE_NAMESPACE, identity_source).hex}"


def _legacy_id(*, kind: str, driver_key: str, name: str) -> str:
    digest = sha1(
        f"{kind}:{driver_key}:{name.casefold()}".encode(), usedforsecurity=False
    ).hexdigest()
    return f"dev_{digest[:24]}"


def _replace_device_id(
    connection: Connection, old_device_id: str, new_device_id: str
) -> None:
    if old_device_id == new_device_id:
        return
    parameters = {"old_id": old_device_id, "new_id": new_device_id}
    connection.execute(
        sa.text(
            "UPDATE device_events SET device_id = :new_id WHERE device_id = :old_id"
        ),
        parameters,
    )
    connection.execute(
        sa.text("UPDATE jobs SET device_id = :new_id WHERE device_id = :old_id"),
        parameters,
    )
    connection.execute(
        sa.text("UPDATE devices SET id = :new_id WHERE id = :old_id"),
        parameters,
    )


def _hex_identifier(value: int | None) -> str:
    return f"{value:04x}" if value is not None else "unknown"
