from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ...printing.commands import DeviceCommand


@dataclass(slots=True, frozen=True)
class DeviceTargetRef:
    """A command target kept for the device-command runtime path."""

    device_id: str | None = None
    printer_name: str | None = None


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


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}
