from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping, TypeGuard

from ...printing.commands import DeviceCommand
from ...printing.jobs import (
    HtmlDocumentContent,
    PdfDocumentContent,
    PrintContent,
    PrintContentKind,
    PrintJob,
    RawDocumentContent,
    ReceiptImageContent,
    StructuredReceiptContent,
    TextDocumentContent,
)
from ...printing.payloads import BinaryPayload, DetectedMediaType
from ...printing.protocols import PrinterTransport

BinaryPayloadSource = Literal["base64", "data_url"]


@dataclass(slots=True, frozen=True)
class DeviceTargetRef:
    device_id: str | None = None
    printer_name: str | None = None


@dataclass(slots=True, frozen=True)
class QueuedPrintOperation:
    target: DeviceTargetRef
    job: PrintJob

    def with_resolved_printer(
        self, *, device_id: str, printer_name: str
    ) -> QueuedPrintOperation:
        return QueuedPrintOperation(
            target=DeviceTargetRef(device_id=device_id, printer_name=printer_name),
            job=replace(self.job, printer_name=printer_name),
        )


@dataclass(slots=True, frozen=True)
class QueuedDeviceCommandOperation:
    target: DeviceTargetRef
    command: DeviceCommand
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def with_resolved_printer(
        self, *, device_id: str, printer_name: str
    ) -> QueuedDeviceCommandOperation:
        return QueuedDeviceCommandOperation(
            target=DeviceTargetRef(device_id=device_id, printer_name=printer_name),
            command=self.command,
            metadata=dict(self.metadata),
        )


def serialize_print_operation(operation: QueuedPrintOperation) -> dict[str, Any]:
    return {
        "target": serialize_target_ref(operation.target),
        "job": serialize_print_job(operation.job),
    }


def deserialize_print_operation(payload: Mapping[str, Any]) -> QueuedPrintOperation:
    target_payload = _mapping(payload.get("target"))
    job_payload = _mapping(payload.get("job"))
    return QueuedPrintOperation(
        target=deserialize_target_ref(target_payload),
        job=deserialize_print_job(job_payload),
    )


def serialize_device_command_operation(
    operation: QueuedDeviceCommandOperation,
) -> dict[str, Any]:
    return {
        "target": serialize_target_ref(operation.target),
        "command": operation.command.to_payload(),
        "metadata": dict(operation.metadata),
    }


def deserialize_device_command_operation(
    payload: Mapping[str, Any],
) -> QueuedDeviceCommandOperation:
    target_payload = _mapping(payload.get("target"))
    command_payload = _mapping(payload.get("command"))
    metadata_payload = _mapping(payload.get("metadata"))
    return QueuedDeviceCommandOperation(
        target=deserialize_target_ref(target_payload),
        command=DeviceCommand.from_payload(command_payload),
        metadata=metadata_payload,
    )


def serialize_target_ref(target: DeviceTargetRef) -> dict[str, Any]:
    return {
        "device_id": target.device_id,
        "printer_name": target.printer_name,
    }


def deserialize_target_ref(payload: Mapping[str, Any]) -> DeviceTargetRef:
    device_id = payload.get("device_id")
    printer_name = payload.get("printer_name")
    return DeviceTargetRef(
        device_id=str(device_id) if device_id is not None else None,
        printer_name=str(printer_name) if printer_name is not None else None,
    )


def serialize_print_job(job: PrintJob) -> dict[str, Any]:
    return {
        "content": serialize_print_content(job.content),
        "printer_name": job.printer_name,
        "transport": job.transport.value,
        "open_drawer": job.open_drawer,
        "metadata": dict(job.metadata),
    }


def deserialize_print_job(payload: Mapping[str, Any]) -> PrintJob:
    content_payload = _mapping(payload.get("content"))
    printer_name = payload.get("printer_name")
    transport = PrinterTransport(
        str(payload.get("transport", PrinterTransport.AUTO.value))
    )
    open_drawer = bool(payload.get("open_drawer", False))
    metadata = _mapping(payload.get("metadata"))
    return PrintJob(
        content=deserialize_print_content(content_payload),
        printer_name=str(printer_name) if printer_name is not None else None,
        transport=transport,
        open_drawer=open_drawer,
        metadata=metadata,
    )


def serialize_print_content(content: PrintContent) -> dict[str, Any]:
    if isinstance(content, StructuredReceiptContent):
        return {
            "kind": content.kind.value,
            "payload": dict(content.payload),
            "document_name": content.document_name,
        }
    if isinstance(content, ReceiptImageContent):
        return {
            "kind": content.kind.value,
            "binary_payload": serialize_binary_payload(content.binary_payload),
            "document_name": content.document_name,
        }
    if isinstance(content, TextDocumentContent):
        return {
            "kind": content.kind.value,
            "text": content.text,
            "document_name": content.document_name,
        }
    if isinstance(content, HtmlDocumentContent):
        return {
            "kind": content.kind.value,
            "html": content.html,
            "document_name": content.document_name,
        }
    if isinstance(content, PdfDocumentContent):
        return {
            "kind": content.kind.value,
            "binary_payload": serialize_binary_payload(content.binary_payload),
            "document_name": content.document_name,
        }
    if isinstance(content, RawDocumentContent):
        return {
            "kind": content.kind.value,
            "binary_payload": serialize_binary_payload(content.binary_payload),
            "data_type": content.data_type,
            "document_name": content.document_name,
        }
    raise TypeError(f"Unsupported print content: {type(content)!r}")


def deserialize_print_content(payload: Mapping[str, Any]) -> PrintContent:
    kind = PrintContentKind(str(payload["kind"]))
    document_name = str(payload.get("document_name", "Document"))
    if kind is PrintContentKind.STRUCTURED_RECEIPT:
        return StructuredReceiptContent(
            payload=_mapping(payload.get("payload")),
            document_name=document_name,
        )
    if kind is PrintContentKind.RECEIPT_IMAGE:
        return ReceiptImageContent(
            binary_payload=deserialize_binary_payload(
                _mapping(payload.get("binary_payload"))
            ),
            document_name=document_name,
        )
    if kind is PrintContentKind.TEXT:
        return TextDocumentContent(
            text=str(payload.get("text", "")),
            document_name=document_name,
        )
    if kind is PrintContentKind.HTML:
        return HtmlDocumentContent(
            html=str(payload.get("html", "")),
            document_name=document_name,
        )
    if kind is PrintContentKind.PDF:
        return PdfDocumentContent(
            binary_payload=deserialize_binary_payload(
                _mapping(payload.get("binary_payload"))
            ),
            document_name=document_name,
        )
    if kind is PrintContentKind.RAW:
        return RawDocumentContent(
            binary_payload=deserialize_binary_payload(
                _mapping(payload.get("binary_payload"))
            ),
            data_type=str(payload.get("data_type", "RAW")),
            document_name=document_name,
        )
    raise ValueError(f"Unsupported print content kind: {kind!r}")


def serialize_binary_payload(payload: BinaryPayload) -> dict[str, Any]:
    detected_type = None
    if payload.detected_type is not None:
        detected_type = {
            "mime_type": payload.detected_type.mime_type,
            "extension": payload.detected_type.extension,
            "description": payload.detected_type.description,
            "confidence": payload.detected_type.confidence,
            "detector": payload.detected_type.detector,
        }
    return {
        "content_base64": b64encode(payload.content).decode("ascii"),
        "source": payload.source,
        "declared_mime_types": list(payload.declared_mime_types),
        "detected_type": detected_type,
    }


def deserialize_binary_payload(payload: Mapping[str, Any]) -> BinaryPayload:
    detected_payload = payload.get("detected_type")
    detected_type = None
    if isinstance(detected_payload, Mapping):
        detected_type = DetectedMediaType(
            mime_type=_optional_text(detected_payload.get("mime_type")),
            extension=_optional_text(detected_payload.get("extension")),
            description=_optional_text(detected_payload.get("description")),
            confidence=float(detected_payload["confidence"])
            if detected_payload.get("confidence") is not None
            else None,
        )
    declared = payload.get("declared_mime_types", ())
    if isinstance(declared, (list, tuple)):
        declared_mime_types = tuple(str(item) for item in declared if item is not None)
    else:
        declared_mime_types = ()
    return BinaryPayload(
        content=b64decode(str(payload.get("content_base64", ""))),
        source=normalize_binary_payload_source(payload.get("source")),
        declared_mime_types=declared_mime_types,
        detected_type=detected_type,
    )


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def is_binary_payload_source(value: object) -> TypeGuard[BinaryPayloadSource]:
    return value in ("base64", "data_url")


def normalize_binary_payload_source(value: object) -> BinaryPayloadSource:
    if is_binary_payload_source(value):
        return value
    return "base64"
