"""Remove Driver identity from persisted Device identifiers."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import unicodedata
from uuid import UUID, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "20260828_0006"
down_revision = "20260827_0005"
branch_labels = None
depends_on = None

_DEVICE_NAMESPACE = UUID("efdbfb52-14ac-5c5c-a01c-b2a846f71d76")
_DEVICE_ID_ENCODING_VERSION = "device-id-v1"
_IDENTITY_ENCODING_VERSION = "device-identity-v1"
_SUPPORTED_KINDS = frozenset({"printer", "scanner", "scale", "display"})
_SUPPORTED_TRANSPORTS = frozenset(
    {"spooler", "network", "usb", "hid", "serial"}
)
_DEVICE_REFERENCE_COLUMNS = frozenset(
    {
        ("device_events", "device_id"),
        ("jobs", "device_id"),
        ("public_print_jobs", "device_id"),
        ("device_work_admissions", "device_id"),
        ("spool_reservations", "device_id"),
    }
)
# `jobs.device_id` is a semantic reference in the 0005 schema. It has no
# declared foreign key, so it is checked and rewritten with the other refs.
_EXPECTED_DEVICE_FOREIGN_KEYS = frozenset(
    {
        ("device_events", "device_id"),
        ("device_work_admissions", "device_id"),
        ("public_print_jobs", "device_id"),
        ("spool_reservations", "device_id"),
    }
)
_JSON_COLUMNS = (
    ("devices", "capabilities_json"),
    ("devices", "metadata_json"),
    ("device_events", "payload_json"),
    ("jobs", "request_json"),
    ("jobs", "request_metadata_json"),
    ("jobs", "result_json"),
    ("job_events", "payload_json"),
    ("gateway_inbound_commands", "payload_json"),
    ("gateway_inbound_commands", "response_json"),
    ("gateway_outbox", "payload_json"),
    ("public_print_jobs", "origin_json"),
    ("public_print_job_events", "snapshot_json"),
)
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 100_000


@dataclass(frozen=True, slots=True)
class _DeviceRow:
    old_id: str
    kind: str
    driver_key: str
    name: str
    identity_transport: str
    identity_serial_number: str | None
    identity_vendor_id: int | None
    identity_product_id: int | None
    identity_os_instance_id: str | None
    identity_port_id: str | None
    first_seen_at: str
    last_seen_at: str
    updated_at: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class _DevicePlan:
    row: _DeviceRow
    new_id: str
    normalized_os_instance_id: str | None


@dataclass(frozen=True, slots=True)
class _JsonUpdate:
    table: str
    rowid: int
    column: str
    value: str


def upgrade() -> None:
    _migrate(direction="upgrade")


def downgrade() -> None:
    _migrate(direction="downgrade")


def _migrate(*, direction: str) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Device identity migration requires SQLite.")

    _drop_device_identity_guards(connection)
    try:
        _validate_device_foreign_keys(connection)
        rows = _read_devices(connection)
        direct_ids = _read_direct_device_ids(connection)
        json_values = _read_and_rewrite_json(
            connection,
            mapping=_plan_mapping(rows, direction=direction),
        )

        if direction == "upgrade":
            plans = _upgrade_plans(rows)
            _validate_generated_ids(plans, direction=direction)
            _apply_upgrade(connection, plans, direct_ids, json_values)
        else:
            plans = _downgrade_plans(rows)
            _validate_generated_ids(plans, direction=direction)
            _apply_downgrade(connection, plans, direct_ids, json_values)

        _assert_foreign_keys(connection)
    finally:
        _create_device_identity_guards(connection)


def _read_devices(connection: Connection) -> tuple[_DeviceRow, ...]:
    result = connection.execute(
        sa.text(
            """
            SELECT id, kind, driver_key, name, identity_transport,
                   identity_serial_number, identity_vendor_id,
                   identity_product_id, identity_os_instance_id,
                   identity_port_id, first_seen_at, last_seen_at, updated_at,
                   is_default
            FROM devices
            ORDER BY id
            """
        )
    )
    rows: list[_DeviceRow] = []
    for row in result.mappings():
        old_id = _required_string(row["id"], "devices.id")
        kind = _required_string(row["kind"], f"devices[{old_id}].kind")
        if kind not in _SUPPORTED_KINDS:
            raise RuntimeError(f"Unsupported Device kind in {old_id!r}.")
        driver_key = _required_string(
            row["driver_key"], f"devices[{old_id}].driver_key"
        )
        name = _required_string(row["name"], f"devices[{old_id}].name")
        transport = _required_string(
            row["identity_transport"], f"devices[{old_id}].identity_transport"
        )
        if transport not in _SUPPORTED_TRANSPORTS:
            raise RuntimeError(f"Unsupported Device transport in {old_id!r}.")
        is_default = row["is_default"]
        if is_default not in (0, 1, False, True):
            raise RuntimeError(f"devices[{old_id}].is_default must be boolean.")
        rows.append(
            _DeviceRow(
                old_id=old_id,
                kind=kind,
                driver_key=driver_key,
                name=name,
                identity_transport=transport,
                identity_serial_number=_optional_string(
                    row["identity_serial_number"],
                    f"devices[{old_id}].identity_serial_number",
                ),
                identity_vendor_id=_optional_identifier(
                    row["identity_vendor_id"],
                    f"devices[{old_id}].identity_vendor_id",
                ),
                identity_product_id=_optional_identifier(
                    row["identity_product_id"],
                    f"devices[{old_id}].identity_product_id",
                ),
                identity_os_instance_id=_optional_string(
                    row["identity_os_instance_id"],
                    f"devices[{old_id}].identity_os_instance_id",
                ),
                identity_port_id=_optional_string(
                    row["identity_port_id"], f"devices[{old_id}].identity_port_id"
                ),
                first_seen_at=_required_string(
                    row["first_seen_at"], f"devices[{old_id}].first_seen_at"
                ),
                last_seen_at=_required_string(
                    row["last_seen_at"], f"devices[{old_id}].last_seen_at"
                ),
                updated_at=_required_string(
                    row["updated_at"], f"devices[{old_id}].updated_at"
                ),
                is_default=bool(is_default),
            )
        )
    for row in rows:
        _parse_timestamp(row.first_seen_at, f"devices[{row.old_id}].first_seen_at")
        _parse_timestamp(row.last_seen_at, f"devices[{row.old_id}].last_seen_at")
        _parse_timestamp(row.updated_at, f"devices[{row.old_id}].updated_at")
    return tuple(rows)


def _read_direct_device_ids(
    connection: Connection,
) -> dict[tuple[str, int], str]:
    ids: dict[tuple[str, int], str] = {}
    known_ids = {
        row[0]
        for row in connection.execute(sa.text("SELECT id FROM devices"))
    }
    for table, column in sorted(_DEVICE_REFERENCE_COLUMNS):
        for row in connection.execute(
            sa.text(f"SELECT rowid, {column} FROM {table}")
        ):
            rowid, device_id = row
            if device_id not in known_ids:
                raise RuntimeError(
                    f"{table}.{column} row {rowid} references an unknown Device."
                )
            ids[(table, int(rowid))] = str(device_id)
    return ids


def _read_and_rewrite_json(
    connection: Connection,
    *,
    mapping: dict[str, str],
) -> tuple[_JsonUpdate, ...]:
    updates: list[_JsonUpdate] = []
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    for table, column in _JSON_COLUMNS:
        if table not in tables:
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column not in columns:
            continue
        for row in connection.execute(
            sa.text(f"SELECT rowid, {column} FROM {table}")
        ):
            rowid, raw = row
            if raw is None:
                continue
            if not isinstance(raw, str):
                raise RuntimeError(f"{table}.{column} row {rowid} is not text JSON.")
            try:
                value = json.loads(raw, parse_constant=_reject_nonfinite)
                rewritten = _rewrite_json_ids(value, mapping)
                encoded = json.dumps(
                    rewritten,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                )
            except (TypeError, ValueError, RecursionError) as exc:
                raise RuntimeError(
                    f"Invalid or too-deep JSON in {table}.{column} row {rowid}."
                ) from exc
            if encoded != raw:
                updates.append(_JsonUpdate(table, int(rowid), column, encoded))
    return tuple(updates)


def _upgrade_plans(rows: tuple[_DeviceRow, ...]) -> tuple[_DevicePlan, ...]:
    plans: list[_DevicePlan] = []
    for row in rows:
        fallback = _normalized_fallback_instance(row)
        identity = _identity_json(row, os_instance_id=fallback)
        stable_key = _stable_key(row, identity)
        plans.append(
            _DevicePlan(
                row=row,
                new_id=_build_device_id(row.kind, stable_key),
                normalized_os_instance_id=fallback,
            )
        )
    return tuple(plans)


def _downgrade_plans(rows: tuple[_DeviceRow, ...]) -> tuple[_DevicePlan, ...]:
    plans: list[_DevicePlan] = []
    for row in rows:
        old_os_instance = _legacy_instance_for_downgrade(row)
        identity_key = _old_0004_identity_key(row, old_os_instance)
        old_id = _old_0004_device_id(row, identity_key)
        plans.append(
            _DevicePlan(
                row=row,
                new_id=old_id,
                normalized_os_instance_id=old_os_instance,
            )
        )
    return tuple(plans)


def _plan_mapping(
    rows: tuple[_DeviceRow, ...], *, direction: str
) -> dict[str, str]:
    plans = _upgrade_plans(rows) if direction == "upgrade" else _downgrade_plans(rows)
    return {plan.row.old_id: plan.new_id for plan in plans}


def _apply_upgrade(
    connection: Connection,
    plans: tuple[_DevicePlan, ...],
    direct_ids: dict[tuple[str, int], str],
    json_values: tuple[_JsonUpdate, ...],
) -> None:
    _apply_device_id_change(
        connection,
        plans=plans,
        direct_ids=direct_ids,
        json_values=json_values,
        merge_duplicates=True,
        update_fallback_identity=True,
    )


def _apply_downgrade(
    connection: Connection,
    plans: tuple[_DevicePlan, ...],
    direct_ids: dict[tuple[str, int], str],
    json_values: tuple[_JsonUpdate, ...],
) -> None:
    _apply_device_id_change(
        connection,
        plans=plans,
        direct_ids=direct_ids,
        json_values=json_values,
        merge_duplicates=False,
        update_fallback_identity=True,
    )


def _apply_device_id_change(
    connection: Connection,
    *,
    plans: tuple[_DevicePlan, ...],
    direct_ids: dict[tuple[str, int], str],
    json_values: tuple[_JsonUpdate, ...],
    merge_duplicates: bool,
    update_fallback_identity: bool,
) -> None:
    connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
    occupied = {plan.row.old_id for plan in plans}
    temporary_ids = _temporary_ids(occupied, len(plans))

    for plan, temporary in zip(plans, temporary_ids, strict=True):
        connection.execute(
            sa.text("UPDATE devices SET id = :temporary WHERE id = :old"),
            {"temporary": temporary, "old": plan.row.old_id},
        )
        for (table, rowid), old_id in direct_ids.items():
            if old_id == plan.row.old_id:
                connection.execute(
                    sa.text(f"UPDATE {table} SET device_id = :temporary WHERE rowid = :rowid"),
                    {"temporary": temporary, "rowid": rowid},
                )

    groups: dict[str, list[tuple[_DevicePlan, str]]] = defaultdict(list)
    for plan, temporary in zip(plans, temporary_ids, strict=True):
        groups[plan.new_id].append((plan, temporary))

    for new_id, group in groups.items():
        winner, winner_temporary = _winner(group)
        aggregate = _aggregate(group) if merge_duplicates else None
        connection.execute(
            sa.text("UPDATE devices SET id = :new_id WHERE id = :temporary"),
            {"new_id": new_id, "temporary": winner_temporary},
        )
        if aggregate is not None:
            connection.execute(
                sa.text(
                    """
                    UPDATE devices
                    SET first_seen_at = :first_seen_at,
                        last_seen_at = :last_seen_at,
                        updated_at = :updated_at,
                        is_default = :is_default
                    WHERE id = :new_id
                    """
                ),
                {"new_id": new_id, **aggregate},
            )
        if update_fallback_identity and winner.normalized_os_instance_id != winner.row.identity_os_instance_id:
            connection.execute(
                sa.text(
                    "UPDATE devices SET identity_os_instance_id = :instance WHERE id = :new_id"
                ),
                {
                    "instance": winner.normalized_os_instance_id,
                    "new_id": new_id,
                },
            )
        for loser, loser_temporary in group:
            if loser_temporary == winner_temporary:
                continue
            if merge_duplicates:
                connection.execute(
                    sa.text("DELETE FROM devices WHERE id = :temporary"),
                    {"temporary": loser_temporary},
                )
            else:
                raise RuntimeError(
                    f"Device identity downgrade produced a duplicate id {new_id!r}."
                )

    for (table, rowid), old_id in direct_ids.items():
        new_id = _mapping_value(plans, old_id)
        connection.execute(
            sa.text(f"UPDATE {table} SET device_id = :new_id WHERE rowid = :rowid"),
            {"new_id": new_id, "rowid": rowid},
        )
    for update in json_values:
        connection.execute(
            sa.text(
                f"UPDATE {update.table} SET {update.column} = :value WHERE rowid = :rowid"
            ),
            {"value": update.value, "rowid": update.rowid},
        )


def _validate_device_foreign_keys(connection: Connection) -> None:
    inspector = sa.inspect(connection)
    actual: set[tuple[str, str]] = set()
    for table in inspector.get_table_names():
        for foreign_key in inspector.get_foreign_keys(table):
            if foreign_key.get("referred_table") != "devices":
                continue
            columns = tuple(foreign_key.get("constrained_columns") or ())
            referred = tuple(foreign_key.get("referred_columns") or ())
            if len(columns) != 1 or referred != ("id",):
                raise RuntimeError(f"Unexpected Device foreign key on {table!r}.")
            actual.add((table, columns[0]))
    if actual != _EXPECTED_DEVICE_FOREIGN_KEYS:
        raise RuntimeError(
            "Unexpected Device foreign keys: "
            f"expected {sorted(_EXPECTED_DEVICE_FOREIGN_KEYS)!r}, found {sorted(actual)!r}."
        )


def _validate_generated_ids(
    plans: tuple[_DevicePlan, ...], *, direction: str
) -> None:
    by_id: dict[str, list[_DevicePlan]] = defaultdict(list)
    for plan in plans:
        by_id[plan.new_id].append(plan)
    if direction == "downgrade":
        collisions = [device_id for device_id, group in by_id.items() if len(group) > 1]
        if collisions:
            raise RuntimeError(
                "Device identity downgrade cannot recover merged rows for generated ids: "
                f"{sorted(collisions)!r}."
            )


def _winner(
    group: list[tuple[_DevicePlan, str]],
) -> tuple[_DevicePlan, str]:
    return min(
        group,
        key=lambda item: (
            -_timestamp_value(item[0].row.updated_at),
            -_timestamp_value(item[0].row.last_seen_at),
            -_timestamp_value(item[0].row.first_seen_at),
            item[0].row.old_id,
        ),
    )


def _aggregate(group: list[tuple[_DevicePlan, str]]) -> dict[str, object]:
    rows = [plan.row for plan, _temporary in group]
    return {
        "first_seen_at": _extreme_timestamp(
            (row.first_seen_at for row in rows), minimum=True
        ),
        "last_seen_at": _extreme_timestamp(
            (row.last_seen_at for row in rows), minimum=False
        ),
        "updated_at": _extreme_timestamp(
            (row.updated_at for row in rows), minimum=False
        ),
        "is_default": int(any(row.is_default for row in rows)),
    }


def _extreme_timestamp(values: Iterable[str], *, minimum: bool) -> str:
    candidates = list(values)
    return (
        min(candidates, key=lambda value: (_timestamp_value(value), value))
        if minimum
        else max(candidates, key=lambda value: (_timestamp_value(value), value))
    )


def _identity_json(row: _DeviceRow, *, os_instance_id: str | None) -> dict[str, object]:
    serial_number = _normalize_optional(row.identity_serial_number)
    os_instance = _normalize_optional(os_instance_id)
    port_id = _normalize_optional(row.identity_port_id)
    if serial_number:
        return {
            "kind": "hardware",
            "product_id": row.identity_product_id,
            "serial_number": serial_number,
            "vendor_id": row.identity_vendor_id,
        }
    if os_instance:
        return {
            "kind": "os",
            "instance_id": os_instance,
            "transport": row.identity_transport,
        }
    if port_id:
        return {
            "kind": "port",
            "port_id": port_id,
            "transport": row.identity_transport,
        }
    raise RuntimeError(f"Device {row.old_id!r} has no stable identity.")


def _stable_key(row: _DeviceRow, identity: dict[str, object]) -> str:
    return _IDENTITY_ENCODING_VERSION + ":" + json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _build_device_id(kind: str, stable_key: str) -> str:
    identity_key = "\0".join((_DEVICE_ID_ENCODING_VERSION, kind, stable_key))
    return f"dev_{uuid5(_DEVICE_NAMESPACE, identity_key).hex}"


def _old_0004_identity_key(row: _DeviceRow, os_instance_id: str | None) -> str:
    # Keep the 0004 byte inputs exact. That revision did not normalize strings.
    if row.identity_serial_number:
        vendor = _hex_identifier(row.identity_vendor_id)
        product = _hex_identifier(row.identity_product_id)
        return f"hardware:{vendor}:{product}:{row.identity_serial_number}"
    if os_instance_id:
        return f"os:{row.identity_transport}:{os_instance_id}"
    if row.identity_port_id:
        return f"port:{row.identity_transport}:{row.identity_port_id}"
    raise RuntimeError(f"Device {row.old_id!r} has no 0004 identity.")


def _old_0004_device_id(row: _DeviceRow, identity_key: str) -> str:
    return f"dev_{uuid5(_DEVICE_NAMESPACE, f'{row.kind}\0{row.driver_key}\0{identity_key}').hex}"


def _legacy_instance_for_downgrade(row: _DeviceRow) -> str | None:
    instance = _normalize_optional(row.identity_os_instance_id)
    if instance and instance.startswith("legacy-name:"):
        if not _normalize_string(row.name):
            raise RuntimeError(f"Device {row.old_id!r} has an empty legacy name.")
        # The downgrade contract preserves the fallback name input verbatim.
        return f"legacy:{row.driver_key}:{row.name}"
    return row.identity_os_instance_id


def _normalized_fallback_instance(row: _DeviceRow) -> str | None:
    instance = _normalize_optional(row.identity_os_instance_id)
    if not instance or not instance.startswith("legacy:"):
        return instance
    normalized_name = _normalize_string(row.name)
    if not normalized_name:
        raise RuntimeError(f"Device {row.old_id!r} has an empty legacy name.")
    return f"legacy-name:{normalized_name}"


def _mapping_value(plans: tuple[_DevicePlan, ...], old_id: str) -> str:
    for plan in plans:
        if plan.row.old_id == old_id:
            return plan.new_id
    raise RuntimeError(f"Missing Device identity mapping for {old_id!r}.")


def _temporary_ids(occupied: set[str], count: int) -> tuple[str, ...]:
    result: list[str] = []
    index = 0
    while len(result) < count:
        candidate = f"__device_id_migration__{index:08x}"
        index += 1
        if candidate in occupied or candidate in result:
            continue
        result.append(candidate)
    return tuple(result)


def _rewrite_json_ids(value: object, mapping: dict[str, str]) -> object:
    nodes = 0

    def visit(current: object, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON value exceeds migration bounds.")
        if isinstance(current, dict):
            rewritten: dict[str, object] = {}
            for key, child in current.items():
                if (
                    isinstance(key, str)
                    and (key == "device_id" or key.endswith("_device_id"))
                    and isinstance(child, str)
                ):
                    rewritten[key] = mapping.get(child, child)
                else:
                    rewritten[key] = visit(child, depth + 1)
            return rewritten
        if isinstance(current, list):
            return [visit(child, depth + 1) for child in current]
        return current

    return visit(value, 0)


def _assert_foreign_keys(connection: Connection) -> None:
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Device identity migration left foreign-key violations: {violations!r}.")


def _drop_device_identity_guards(connection: Connection) -> None:
    for name in (
        "ck_spool_reservations_identity_immutable",
        "ck_device_work_admissions_identity_immutable",
    ):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")


def _create_device_identity_guards(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TRIGGER ck_device_work_admissions_identity_immutable
        BEFORE UPDATE OF id, planned_job_id, database, scope_kind, organization_id,
            site_id, pos_configuration_id, paired_client_id, idempotency_key,
            fingerprint, intent_id, device_id, deadline_at, original_size_bytes,
            created_at, actor_id, binding_revision_id, authorization_digest,
            operation, media_type, normalized_options_digest, grant_scope_digest,
            origin_submission_key, origin_kind, origin_json, contract_major,
            copy_ordinal ON device_work_admissions
        BEGIN
            SELECT RAISE(ABORT, 'admission identity is immutable');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER ck_spool_reservations_identity_immutable
        BEFORE UPDATE OF id, reservation_kind, admission_id, device_id, owner_id,
            owner_generation, queue_slots, original_bytes, persistent_bytes,
            temporary_bytes, created_at ON spool_reservations
        BEGIN
            SELECT RAISE(ABORT, 'reservation identity is immutable');
        END
        """
    )


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{field} must be a non-empty string.")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError(f"{field} must be a string or null.")
    return value


def _optional_identifier(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise RuntimeError(f"{field} must be an unsigned 16-bit integer or null.")
    return value


def _normalize_string(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_string(value)
    return normalized or None


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _timestamp_value(value: str) -> float:
    return _parse_timestamp(value, "timestamp").timestamp()


def _hex_identifier(value: object) -> str:
    return f"{value:04x}" if isinstance(value, int) else "unknown"


def _reject_nonfinite(value: str) -> object:
    raise ValueError(f"Non-finite JSON number {value!r}.")
