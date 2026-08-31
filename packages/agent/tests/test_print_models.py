from __future__ import annotations

import pytest
from pydantic import ValidationError

from inari.local_api.schemas import DeviceCommandRequest, ReceiptImageEnvelope
from inari.printing.commands import CutPaper


def receipt_envelope() -> dict[str, object]:
    return {
        "contract_major": 1,
        "operation": "receipt_image",
        "media_type": "image/jpeg",
        "context": {
            "contract_major": 1,
            "print_intent_id": "pi_v1_test",
            "origin_submission_key": "osk_v1_test",
            "origin": {
                "kind": "pos",
                "pos_session_id": "pos_session_42",
                "offline_order_id": "01991a84-d0c2-7a49-89ad-2fd14bdbe501",
                "server_order_id": None,
                "document_kind": "customer_receipt",
                "content_revision": "sha256:receipt-revision",
            },
            "binding_revision_id": "binding_revision_9",
            "device_id": "dev_receipt_1",
            "copy_ordinal": 1,
        },
    }


def test_receipt_envelope_accepts_only_work_specific_values() -> None:
    envelope = ReceiptImageEnvelope.model_validate(receipt_envelope())

    assert envelope.context.print_intent_id == "pi_v1_test"
    assert envelope.context.device_id == "dev_receipt_1"
    assert envelope.context.origin.pos_session_id == "pos_session_42"


def test_preparation_envelope_keeps_the_exact_segment_identity() -> None:
    payload = receipt_envelope()
    context = payload["context"]
    assert isinstance(context, dict)
    context["origin"] = {
        "kind": "preparation",
        "pos_session_id": "pos_session_42",
        "offline_order_id": "01991a84-d0c2-7a49-89ad-2fd14bdbe501",
        "server_order_id": None,
        "document_kind": "preparation_ticket",
        "content_revision": "sha256:ticket-image",
        "segment_kind": "cancelled",
        "segment_index": 1,
        "preparation_revision": "sha256:order-change",
    }

    envelope = ReceiptImageEnvelope.model_validate(payload)

    origin = envelope.context.origin
    assert origin.kind == "preparation"
    assert origin.segment_kind == "cancelled"
    assert origin.segment_index == 1


@pytest.mark.parametrize(
    "obsolete_field, value",
    (
        ("printer_name", "Kitchen Printer"),
        ("transport", "raw"),
        ("open_cash_drawer", True),
        ("base64", "Zm9v"),
    ),
)
def test_receipt_envelope_rejects_obsolete_print_fields(
    obsolete_field: str,
    value: object,
) -> None:
    payload = receipt_envelope()
    payload[obsolete_field] = value

    with pytest.raises(ValidationError):
        ReceiptImageEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    "authority_field",
    (
        "organization_id",
        "site_id",
        "database",
        "pos_configuration_id",
        "paired_client_id",
        "actor_id",
        "grant_id",
        "authorization_digest",
    ),
)
def test_receipt_context_rejects_client_supplied_authority(
    authority_field: str,
) -> None:
    payload = receipt_envelope()
    context = payload["context"]
    assert isinstance(context, dict)
    context[authority_field] = "untrusted"

    with pytest.raises(ValidationError):
        ReceiptImageEnvelope.model_validate(payload)


def test_device_command_targets_only_a_stable_device_id() -> None:
    request = DeviceCommandRequest.model_validate(
        {
            "target": {"device_id": "dev_test"},
            "command": {"kind": "cut_paper", "mode": "full"},
        }
    )

    operation = request.to_operation()

    assert operation.target.device_id == "dev_test"
    assert isinstance(operation.command, CutPaper)
    assert operation.command.mode == "full"


def test_device_command_rejects_printer_name_target() -> None:
    with pytest.raises(ValidationError):
        DeviceCommandRequest.model_validate(
            {
                "target": {
                    "device_id": "dev_test",
                    "printer_name": "Kitchen Printer",
                },
                "command": {"kind": "cut_paper", "mode": "full"},
            }
        )
