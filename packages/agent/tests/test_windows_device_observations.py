from types import SimpleNamespace
from datetime import UTC, datetime

import pytest

from inari.device_authority.authority import verify_signed
from inari.device_authority.models import SignerPurpose
from inari.device_authority.observations import (
    DeviceObservationSigningKey,
    LiveDeviceObservationReader,
)
from inari.printing.drivers.windows import WindowsPrinterDriver, WindowsSpooler
from inari.runtime.models import DeviceRecord
from inari.security.secrets import MemorySecretStore

from .test_device_observations import Authority, Devices


class PrinterAPI:
    def __init__(self, *, status=0, fields=0x8, width=800, port="USB002"):
        self.information = {
            "pPrinterName": "Receipt",
            "pPortName": port,
            "pDriverName": "Actual printer driver",
            "pDevMode": SimpleNamespace(Fields=fields, PaperWidth=width),
            "Status": status,
        }
        self.closed = []
        self.fail = False

    def GetDefaultPrinter(self):
        return "Receipt"

    def OpenPrinter(self, name):
        return name

    def ClosePrinter(self, handle):
        self.closed.append(handle)

    def GetPrinter(self, handle, level):
        assert level == 2
        if self.fail:
            raise OSError("The queue disappeared.")
        return self.information


def driver(api):
    return WindowsPrinterDriver(spooler=WindowsSpooler(api))


def test_discovery_reads_current_spooler_facts_without_inventing_firmware(monkeypatch):
    monkeypatch.setattr(
        "inari.printing.drivers.windows.platform.version", lambda: "10.0.26300"
    )
    api = PrinterAPI()
    discovered = driver(api)
    device = discovered.get_device("Receipt")
    assert device.metadata["authority_observation"] == {
        "platform_backend_id": "windows-spooler",
        "firmware_version": "unavailable",
        "firmware_build": "unavailable",
        "connection": "usb",
        "media_profile": "80mm",
        "operating_system": "windows:10.0.26300",
        "ready": True,
    }
    api.information["Status"] = 0x80
    assert not discovered.get_device("Receipt").metadata["authority_observation"][
        "ready"
    ]
    assert api.closed == ["Receipt", "Receipt"]


def test_windows_discovery_produces_a_ready_signed_observation(monkeypatch):
    monkeypatch.setattr(
        "inari.printing.drivers.windows.platform.version", lambda: "10.0.26300"
    )
    device = DeviceRecord.from_printer(driver(PrinterAPI()).get_device("Receipt"))
    key = DeviceObservationSigningKey(MemorySecretStore())
    authority = Authority(key)
    reader = LiveDeviceObservationReader(
        devices=Devices(device), authority=authority, signing_key=key
    )

    signed = reader.read_current(device.id, "a" * 64)

    assert signed is not None
    assert signed.observation.ready
    assert signed.observation.firmware_version == "unavailable"
    assert signed.observation.firmware_build == "unavailable"
    assert signed.observation.platform_backend_id == "windows-spooler"
    assert signed.observation.connection == "usb"
    assert signed.observation.media_profile == "80mm"
    assert signed.observation.operating_system == "windows:10.0.26300"
    verify_signed(
        payload=signed.observation,
        digest=signed.digest,
        signer=authority.signer,
        signature=signed.signature,
        purpose=SignerPurpose.DEVICE_OBSERVATION,
        now=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "status", [None, True, -1, 0x80, 0x10, 0x400000, 0x800000, 0x80000000]
)
def test_unknown_or_blocked_spooler_status_cannot_claim_ready(status):
    assert (
        not driver(PrinterAPI(status=status))
        .get_device("Receipt")
        .metadata["authority_observation"]["ready"]
    )


@pytest.mark.parametrize("fields,width", [(0, 800), (True, 800), (0x8, 0), (0x8, True)])
def test_media_width_requires_an_initialized_devmode_field(fields, width):
    observation = (
        driver(PrinterAPI(fields=fields, width=width))
        .get_device("Receipt")
        .metadata["authority_observation"]
    )
    assert observation["media_profile"] == "unavailable"


@pytest.mark.parametrize("port", ["USB002,USB003", "WSD-USB002", "FILE:", "", None])
def test_unknown_or_pooled_ports_do_not_claim_a_usb_connection(port):
    observation = (
        driver(PrinterAPI(port=port))
        .get_device("Receipt")
        .metadata["authority_observation"]
    )
    assert observation["connection"] == "unavailable"


def test_failed_observation_closes_the_handle_and_preserves_discovery():
    api = PrinterAPI()
    api.fail = True
    observation = driver(api).get_device("Receipt").metadata["authority_observation"]
    assert not observation["ready"]
    assert observation["media_profile"] == "unavailable"
    assert observation["connection"] == "unavailable"
    assert api.closed == ["Receipt"]
