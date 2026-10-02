from copy import deepcopy

import pytest

from inari_odoo_services.report_requests import (
    ReportRequestError,
    check_report_action,
    parse_report_request,
)


@pytest.fixture
def report_request():
    return {
        "version": 1,
        "binding_id": 2,
        "binding_revision_id": 3,
        "report_action_id": 4,
        "source_model": "stock.picking",
        "source_ids": [7, 5],
        "site_id": 8,
        "report_type": "qweb-pdf",
        "copies": 1,
        "wizard_data": {},
    }


def test_preserves_source_order(report_request):
    assert parse_report_request(report_request).source_ids == (7, 5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("binding_id", True),
        ("binding_revision_id", 0),
        ("report_action_id", "4"),
        ("site_id", -1),
        ("copies", True),
        ("copies", 11),
        ("source_model", "stock..picking"),
        ("source_model", "stock._private"),
        ("source_model", "stóck.picking"),
        ("source_ids", []),
        ("source_ids", [5, 5]),
        ("source_ids", [True]),
        ("source_ids", list(range(1, 502))),
        ("report_type", "qweb-html"),
        ("report_type", {}),
        ("wizard_data", {"context": {"sudo": True}}),
        ("wizard_data", []),
    ],
)
def test_rejects_invalid_fields(report_request, field, value):
    report_request[field] = value
    with pytest.raises(ReportRequestError):
        parse_report_request(report_request)


@pytest.mark.parametrize(
    "field",
    [
        "device_id",
        "idempotency_key",
        "copy_ordinal",
        "document_index",
        "sequence_id",
        "content",
    ],
)
def test_browser_cannot_supply_server_authority(report_request, field):
    report_request[field] = "injected"
    with pytest.raises(ReportRequestError):
        parse_report_request(report_request)


@pytest.fixture
def action():
    return {
        "type": "ir.actions.client",
        "tag": "inari_report_print",
        "context": {
            "inari_managed_report": True,
            "inari_report_binding_id": 2,
            "inari_binding_revision_id": 3,
        },
    }


def test_accepts_matching_trusted_action(report_request, action):
    check_report_action(parse_report_request(report_request), action)


@pytest.mark.parametrize(
    "field,value",
    [
        ("inari_managed_report", 1),
        ("inari_report_binding_id", 9),
        ("inari_report_binding_id", True),
        ("inari_binding_revision_id", 2),
        ("inari_binding_revision_id", "3"),
    ],
)
def test_rejects_stale_or_forged_action(report_request, action, field, value):
    altered = deepcopy(action)
    altered["context"][field] = value
    with pytest.raises(ReportRequestError):
        check_report_action(parse_report_request(report_request), altered)


def test_does_not_evaluate_action_context(report_request, action):
    action["context"] = "dict(inari_managed_report=True)"
    with pytest.raises(ReportRequestError):
        check_report_action(parse_report_request(report_request), action)
