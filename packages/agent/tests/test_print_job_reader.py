from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from inari.db.schema import metadata
from inari.print_jobs import PairedClientScope, PrintIntentQuery, PreparationPrintOrigin
from inari.print_jobs.sqlite import SqlitePrintJobReader
from inari.runtime.store import RuntimeStore


NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _store(tmp_path: Path) -> RuntimeStore:
    store = RuntimeStore(tmp_path / "agent.sqlite3")
    metadata.create_all(store.engine)
    return store


def _origin(*, kind: str = "pos", pairing_id: str = "pairing_1") -> dict[str, object]:
    values: dict[str, object] = {
        "organization_id": "org_1",
        "site_id": "site_1",
        "database": "odoo",
        "paired_client_id": pairing_id,
        "pos_configuration_id": "pos_1",
        "pos_session_id": "session_1",
        "offline_order_id": "order_1",
        "server_order_id": None,
        "document_kind": "customer_receipt",
        "content_revision": "sha256:receipt",
    }
    if kind == "preparation":
        values.update(
            {
                "document_kind": "preparation_ticket",
                "segment_kind": "new",
                "segment_index": 0,
                "preparation_revision": "sha256:preparation",
            }
        )
    return values


def _seed_job(
    database_path: Path,
    *,
    job_id: str,
    intent_id: str,
    pairing_id: str = "pairing_1",
    origin_kind: str = "pos",
    event_type: str = "accepted",
) -> None:
    accepted_at = NOW.isoformat().replace("+00:00", "Z")
    expires_at = (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    origin = _origin(kind=origin_kind, pairing_id=pairing_id)
    snapshot = {
        "accepted_at": accepted_at,
        "contract_version": "v1",
        "device_id": "device_1",
        "intent_id": intent_id,
        "job_id": job_id,
        "state": "accepted",
        "state_version": 1,
    }
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO public_print_jobs (
                id, admission_id, intent_id, device_id, scope_kind,
                organization_id, site_id, pos_configuration_id, paired_client_id,
                origin_kind, origin_json, state, state_version, accepted_at,
                expires_at, retryable, contract_version
            ) VALUES (?, ?, ?, 'device_1', 'paired_client', 'org_1', 'site_1',
                      'pos_1', ?, ?, ?, 'accepted', 1, ?, ?, 0, 'v1')
            """,
            (
                job_id,
                f"admission_{job_id}",
                intent_id,
                pairing_id,
                origin_kind,
                json.dumps(origin, sort_keys=True, separators=(",", ":")),
                accepted_at,
                expires_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO public_print_job_events (
                job_id, state_version, event_type, snapshot_json, occurred_at
            ) VALUES (?, 1, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                accepted_at,
            ),
        )


def _scope() -> PairedClientScope:
    return PairedClientScope(
        organization_id="org_1",
        site_id="site_1",
        pos_configuration_id="pos_1",
        paired_client_id="pairing_1",
    )


@pytest.mark.anyio
async def test_reconcile_returns_scoped_jobs_in_request_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_job(
        store.database_path,
        job_id="job_receipt",
        intent_id="intent_receipt",
    )
    _seed_job(
        store.database_path,
        job_id="job_hidden",
        intent_id="intent_hidden",
        pairing_id="pairing_2",
    )
    _seed_job(
        store.database_path,
        job_id="job_preparation",
        intent_id="intent_preparation",
        origin_kind="preparation",
    )

    page = await SqlitePrintJobReader(store).reconcile(
        PrintIntentQuery.from_ids(
            ["intent_preparation", "intent_hidden", "intent_missing", "intent_receipt"],
            scope=_scope(),
        )
    )

    assert [job.intent_id for job in page.jobs] == [
        "intent_preparation",
        "intent_receipt",
    ]
    assert page.missing_print_intent_ids == ("intent_hidden", "intent_missing")
    assert page.high_water_mark == 3
    preparation = page.jobs[0]
    assert isinstance(preparation.origin, PreparationPrintOrigin)
    assert preparation.origin.segment_kind == "new"
    assert preparation.origin.segment_index == 0


@pytest.mark.anyio
async def test_reconcile_high_water_mark_excludes_other_client_events(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_job(
        store.database_path,
        job_id="job_visible",
        intent_id="intent_visible",
    )
    _seed_job(
        store.database_path,
        job_id="job_hidden",
        intent_id="intent_hidden",
        pairing_id="pairing_2",
    )

    page = await SqlitePrintJobReader(store).reconcile(
        PrintIntentQuery.from_ids(["intent_visible"], scope=_scope())
    )

    assert page.high_water_mark == 1


@pytest.mark.anyio
async def test_reconcile_deduplicates_print_intent_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_job(
        store.database_path,
        job_id="job_visible",
        intent_id="intent_visible",
    )

    query = PrintIntentQuery.from_ids(
        ["intent_visible", "intent_visible"], scope=_scope()
    )
    page = await SqlitePrintJobReader(store).reconcile(query)

    assert query.print_intent_ids == ("intent_visible",)
    assert [job.job_id for job in page.jobs] == ["job_visible"]
