from __future__ import annotations

import unittest

from iot_agent.device_commands import CutPaper, DeviceCommand, FeedDots, FeedLines, PrintTestPage
from iot_agent.printers import CutMode, PrinterTransport
from iot_agent.runtime.operations import (
    DeviceTargetRef,
    QueuedDeviceCommandOperation,
    deserialize_device_command_operation,
    serialize_device_command_operation,
)


class DeviceCommandTests(unittest.TestCase):
    def test_device_command_roundtrip_uses_kind_registry(self) -> None:
        payload = PrintTestPage(transport=PrinterTransport.RAW).to_payload()

        command = DeviceCommand.from_payload(payload)

        self.assertIsInstance(command, PrintTestPage)
        self.assertEqual(command.transport, PrinterTransport.RAW)

    def test_feed_commands_validate_positive_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            FeedLines(count=0)

        with self.assertRaisesRegex(ValueError, "greater than zero"):
            FeedDots(count=0)

    def test_command_operation_roundtrip_preserves_concrete_command(self) -> None:
        operation = QueuedDeviceCommandOperation(
            target=DeviceTargetRef(device_id="dev_123", printer_name="Kitchen Printer"),
            command=CutPaper(mode=CutMode.FULL),
            metadata={"source": "test"},
        )

        restored = deserialize_device_command_operation(serialize_device_command_operation(operation))

        self.assertEqual(restored.target.device_id, "dev_123")
        self.assertEqual(restored.target.printer_name, "Kitchen Printer")
        self.assertIsInstance(restored.command, CutPaper)
        self.assertEqual(restored.command.mode, CutMode.FULL)
        self.assertEqual(restored.metadata, {"source": "test"})


if __name__ == "__main__":
    unittest.main()
