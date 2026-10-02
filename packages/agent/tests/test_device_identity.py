from __future__ import annotations

import pytest

from inari.drivers import DeviceIdentity, DeviceKind, DeviceTransport
from inari.printing.protocols import (
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from inari.runtime.models import DeviceRecord, build_device_id


def _identity() -> DeviceIdentity:
    return DeviceIdentity(
        transport=DeviceTransport.USB,
        serial_number="SN-42",
        vendor_id=0x04B8,
        product_id=0x0E28,
    )


def test_device_id_is_stable_when_the_driver_changes() -> None:
    identity = _identity()
    capabilities = PrinterCapabilities(
        raw=True,
        text=False,
        documents=False,
        cash_drawer=False,
    )
    first = PrinterDevice(
        name="Receipt Printer",
        driver_key="driver-a",
        identity=identity,
        is_default=True,
        preferred_transport=PrinterTransport.RAW,
        capabilities=capabilities,
    )
    second = PrinterDevice(
        name="Receipt Printer",
        driver_key="driver-b",
        identity=identity,
        is_default=True,
        preferred_transport=PrinterTransport.RAW,
        capabilities=capabilities,
    )

    assert DeviceRecord.from_printer(first).id == DeviceRecord.from_printer(second).id


def test_device_id_distinguishes_device_kinds() -> None:
    identity = _identity()

    assert build_device_id(kind=DeviceKind.PRINTER, identity=identity) != build_device_id(
        kind=DeviceKind.SCALE, identity=identity
    )


def test_device_id_uses_a_versioned_canonical_identity() -> None:
    identity = _identity()
    changed_serial = DeviceIdentity(
        transport=identity.transport,
        serial_number="SN-43",
        vendor_id=identity.vendor_id,
        product_id=identity.product_id,
    )

    assert build_device_id(kind=DeviceKind.PRINTER, identity=identity) != build_device_id(
        kind=DeviceKind.PRINTER, identity=changed_serial
    )

    assert identity.stable_key() == (
        'device-identity-v1:{"kind":"hardware","product_id":3624,'
        '"serial_number":"SN-42","vendor_id":1208}'
    )
    assert (
        build_device_id(kind=DeviceKind.PRINTER, identity=identity)
        == "dev_e7342d26d1bb5d86ac50b49cb830b26b"
    )


def test_device_identity_requires_an_immutable_identifier() -> None:
    identity = DeviceIdentity(transport=DeviceTransport.USB)

    with pytest.raises(ValueError, match="stable"):
        build_device_id(kind=DeviceKind.PRINTER, identity=identity)


def test_identity_normalizes_text_and_uses_documented_precedence() -> None:
    identity = DeviceIdentity(
        transport=DeviceTransport.USB,
        serial_number="  SN-42  ",
        vendor_id=0x04B8,
        product_id=0x0E28,
        os_instance_id="ignored-os-id",
        port_id="ignored-port",
    )

    assert identity.stable_key() == _identity().stable_key()


def test_os_and_port_identity_paths_are_stable_and_distinct() -> None:
    os_identity = DeviceIdentity(
        transport=DeviceTransport.SPOOLER,
        os_instance_id="cups:receipt",
    )
    same_os_identity = DeviceIdentity(
        transport=DeviceTransport.SPOOLER,
        os_instance_id=" cups:receipt ",
        port_id="ignored",
    )
    port_identity = DeviceIdentity(
        transport=DeviceTransport.SERIAL,
        port_id="/dev/ttyS0",
    )

    assert os_identity.stable_key() == same_os_identity.stable_key()
    assert os_identity.stable_key() != port_identity.stable_key()


def test_hardware_identity_stays_stable_across_transport_changes() -> None:
    usb = _identity()
    spooler = DeviceIdentity(
        transport=DeviceTransport.SPOOLER,
        serial_number=usb.serial_number,
        vendor_id=usb.vendor_id,
        product_id=usb.product_id,
    )

    assert usb.stable_key() == spooler.stable_key()


def test_identity_rejects_invalid_runtime_values() -> None:
    with pytest.raises(TypeError, match="vendor_id"):
        DeviceIdentity(
            transport=DeviceTransport.USB,
            serial_number="SN",
            vendor_id=True,
        )
    with pytest.raises(ValueError, match="product_id"):
        DeviceIdentity(
            transport=DeviceTransport.USB,
            serial_number="SN",
            product_id=65_536,
        )
