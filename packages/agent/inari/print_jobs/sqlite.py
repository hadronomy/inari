from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping

from ..db.schema import public_print_job_events_table, public_print_jobs_table
from ..runtime.store import RuntimeStore
from .models import (
    PairedClientScope,
    PosPrintOrigin,
    PreparationPrintOrigin,
    PrintIntentPage,
    PrintIntentQuery,
    PrintJob,
    PrintJobScope,
    PrintJobState,
    ReportPrintOrigin,
    SiteManagerScope,
)


class SqlitePrintJobReader:
    """Read content-free Print Job projections from the durable Agent ledger."""

    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    async def get(self, job_id: str, *, scope: PrintJobScope) -> PrintJob | None:
        with self._store.connection() as connection:
            row = (
                connection.execute(
                    select(public_print_jobs_table).where(
                        public_print_jobs_table.c.id == job_id,
                        *_scope_predicates(scope),
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _job_from_row(row)

    async def reconcile(self, query: PrintIntentQuery) -> PrintIntentPage:
        predicates = _scope_predicates(query.scope)
        with self._store.connection() as connection:
            rows = (
                connection.execute(
                    select(public_print_jobs_table).where(
                        public_print_jobs_table.c.intent_id.in_(query.print_intent_ids),
                        *predicates,
                    )
                )
                .mappings()
                .all()
            )
            high_water_mark = connection.execute(
                select(
                    func.coalesce(func.max(public_print_job_events_table.c.sequence), 0)
                )
                .select_from(
                    public_print_job_events_table.join(
                        public_print_jobs_table,
                        public_print_job_events_table.c.job_id
                        == public_print_jobs_table.c.id,
                    )
                )
                .where(*predicates)
            ).scalar_one()

        jobs_by_intent = {str(row["intent_id"]): _job_from_row(row) for row in rows}
        jobs = tuple(
            jobs_by_intent[print_intent_id]
            for print_intent_id in query.print_intent_ids
            if print_intent_id in jobs_by_intent
        )
        missing = tuple(
            print_intent_id
            for print_intent_id in query.print_intent_ids
            if print_intent_id not in jobs_by_intent
        )
        return PrintIntentPage(
            jobs=jobs,
            missing_print_intent_ids=missing,
            high_water_mark=int(high_water_mark),
        )


def _scope_predicates(
    scope: PairedClientScope | SiteManagerScope,
) -> tuple[Any, ...]:
    predicates: list[Any] = [
        public_print_jobs_table.c.organization_id == scope.organization_id,
        public_print_jobs_table.c.site_id == scope.site_id,
    ]
    if isinstance(scope, PairedClientScope):
        predicates.extend(
            (
                public_print_jobs_table.c.scope_kind == "paired_client",
                public_print_jobs_table.c.pos_configuration_id
                == scope.pos_configuration_id,
                public_print_jobs_table.c.paired_client_id == scope.paired_client_id,
            )
        )
    elif scope.pos_configuration_id is not None:
        predicates.extend(
            (
                public_print_jobs_table.c.pos_configuration_id
                == scope.pos_configuration_id,
                public_print_jobs_table.c.paired_client_id == scope.paired_client_id,
            )
        )
    return tuple(predicates)


def _job_from_row(row: RowMapping) -> PrintJob:
    origin_values = _origin_values(row["origin_json"])
    origin_kind = str(row["origin_kind"])
    if origin_kind == "pos":
        origin = PosPrintOrigin(**origin_values)
    elif origin_kind == "preparation":
        origin = PreparationPrintOrigin(**origin_values)
    elif origin_kind == "report":
        origin = _report_origin(row, origin_values)
    else:
        raise RuntimeError("The stored Print Job has an unsupported origin kind.")
    return PrintJob(
        job_id=str(row["id"]),
        intent_id=str(row["intent_id"]),
        device_id=str(row["device_id"]),
        origin=origin,
        managed_work_id=_optional_text(row["managed_work_id"]),
        state=PrintJobState(str(row["state"])),
        state_version=int(row["state_version"]),
        accepted_at=_timestamp(row["accepted_at"]),
        started_at=_optional_timestamp(row["started_at"]),
        terminal_at=_optional_timestamp(row["terminal_at"]),
        expires_at=_timestamp(row["expires_at"]),
        retryable=bool(row["retryable"]),
        error_code=_optional_text(row["error_code"]),
        message_key=_optional_text(row["message_key"]),
        confirmation_evidence=_optional_text(row["confirmation_evidence"]),
        contract_version=str(row["contract_version"]),
    )


def _report_origin(row: RowMapping, values: Mapping[str, Any]) -> ReportPrintOrigin:
    if any(
        values.get(name) != row[name]
        for name in ("organization_id", "site_id", "managed_work_id")
    ):
        raise RuntimeError("The stored Report Origin does not match its Print Job.")
    binding = values.get("binding")
    source = values.get("source")
    if not isinstance(binding, dict) or not isinstance(source, dict):
        raise RuntimeError("The stored Report Origin has no Binding or source.")
    if source.get("kind") == "records":
        identifiers = source.get("ordered_ids")
        if (
            not isinstance(identifiers, list)
            or not identifiers
            or any(type(value) is not int or value <= 0 for value in identifiers)
        ):
            raise RuntimeError("The stored Report Origin has invalid record IDs.")
        record_ids = tuple(str(value) for value in identifiers)
        wizard_input_digest = None
    elif source.get("kind") == "wizard":
        record_ids = ()
        wizard_input_digest = source.get("input_digest")
    else:
        raise RuntimeError("The stored Report Origin has an unsupported source.")
    return ReportPrintOrigin(
        organization_id=values["organization_id"],
        site_id=values["site_id"],
        database=values["database"],
        company_id=values["company_id"],
        report_binding_id=binding["report_binding_id"],
        report_route=values["route"],
        report_action=binding["report_action_id"],
        source_model=source["model"],
        record_ids=record_ids,
        wizard_input_digest=wizard_input_digest,
        rendered_document_index=values["rendered_document_index"],
    )


def _origin_values(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RuntimeError("The stored Print Job origin is not JSON text.")
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError("The stored Print Job origin is invalid JSON.") from error
    if not isinstance(loaded, Mapping):
        raise RuntimeError("The stored Print Job origin is not an object.")
    return dict(loaded)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError("The stored Print Job timestamp is not text.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("The stored Print Job timestamp is invalid.") from error
    if parsed.tzinfo is None:
        raise RuntimeError("The stored Print Job timestamp has no time zone.")
    return parsed.astimezone(UTC)


def _optional_timestamp(value: object) -> datetime | None:
    return None if value is None else _timestamp(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("The stored Print Job value is not text.")
    return value


__all__ = ["SqlitePrintJobReader"]
