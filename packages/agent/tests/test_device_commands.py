from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from inari.device_commands import (
    CutPaper,
    DeviceCommand,
    FeedDots,
    FeedLines,
    PrintTestPage,
)
from inari.printers import CutMode, PrinterTransport
from inari.runtime.operations import (
    DeviceTargetRef,
    QueuedDeviceCommandOperation,
    deserialize_device_command_operation,
    serialize_device_command_operation,
)


def test_device_command_roundtrip_uses_kind_registry() -> None:
    payload = PrintTestPage(transport=PrinterTransport.RAW).to_payload()

    command = DeviceCommand.from_payload(payload)

    assert isinstance(command, PrintTestPage)
    assert command.transport is PrinterTransport.RAW


@pytest.mark.parametrize("command_type", [FeedLines, FeedDots])
def test_feed_commands_validate_positive_counts(
    command_type: type[FeedLines] | type[FeedDots],
) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        command_type(count=0)


@given(st.integers(min_value=1, max_value=10_000))
def test_feed_lines_roundtrip_preserves_count(count: int) -> None:
    restored = DeviceCommand.from_payload(FeedLines(count=count).to_payload())

    assert restored == FeedLines(count=count)


def test_command_operation_roundtrip_preserves_concrete_command() -> None:
    operation = QueuedDeviceCommandOperation(
        target=DeviceTargetRef(device_id="dev_123", printer_name="Kitchen Printer"),
        command=CutPaper(mode=CutMode.FULL),
        metadata={"source": "test"},
    )

    restored = deserialize_device_command_operation(
        serialize_device_command_operation(operation)
    )

    assert restored.target.device_id == "dev_123"
    assert restored.target.printer_name == "Kitchen Printer"
    assert isinstance(restored.command, CutPaper)
    assert restored.command.mode is CutMode.FULL
    assert restored.metadata == {"source": "test"}
