from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

from inari.device_authority.authority import verify_signed
from inari.device_authority.models import SignerPurpose, SignerRecord, SignerState
from inari.device_authority.observations import (
    DeviceObservationSigningKey,
    LiveDeviceObservationReader,
)
from inari.drivers import DeviceIdentity, DeviceTransport
from inari.printing.protocols import PrinterDevice
from inari.runtime.models import DeviceConnectionState, DeviceRecord
from inari.security.secrets import MemorySecretStore


class Authority:
    def __init__(self, key: DeviceObservationSigningKey) -> None:
        self.signer = SignerRecord(
            key_id=key.key_id(),
            purpose=SignerPurpose.DEVICE_OBSERVATION,
            public_key=key.public_key(),
            state=SignerState.ACTIVE,
            not_before=datetime.now(UTC) - timedelta(minutes=1),
            not_after=datetime.now(UTC) + timedelta(days=1),
            retired_at=None,
        )

    def read_signer(self, key_id, purpose):
        if key_id == self.signer.key_id and purpose is self.signer.purpose:
            return self.signer
        return None

    def read_active_certification_row(self, device_id):
        return SimpleNamespace(row=SimpleNamespace(driver_profile_digest="a" * 64))


class Devices:
    def __init__(self, device: DeviceRecord) -> None:
        self.device = device

    def get(self, device_id):
        return self.device if device_id == self.device.id else None


def discovered_device(*, facts: dict | None = None) -> DeviceRecord:
    printer = PrinterDevice(
        name="Receipt queue",
        driver_key="windows.printers",
        identity=DeviceIdentity(
            transport=DeviceTransport.SPOOLER,
            os_instance_id="windows-queue:Receipt queue",
        ),
        metadata={"authority_observation": facts} if facts is not None else {},
    )
    return DeviceRecord.from_printer(printer)


def test_live_observation_is_signed_by_its_purpose_key():
    key = DeviceObservationSigningKey(MemorySecretStore())
    authority = Authority(key)
    facts = {
        "platform_backend_id": "windows-spooler",
        "connection": "usb",
        "media_profile": "80mm",
        "firmware_version": "1.2",
        "firmware_build": "3",
        "operating_system": "windows",
        "ready": True,
    }
    device = discovered_device(facts=facts)
    devices = Devices(device)
    reader = LiveDeviceObservationReader(
        devices=devices, authority=authority, signing_key=key
    )

    signed = reader.read_current(device.id)

    assert signed.observation.ready
    assert signed.observation.device_id == device.id
    verify_signed(
        payload=signed.observation,
        digest=signed.digest,
        signer=authority.signer,
        signature=signed.signature,
        purpose=SignerPurpose.DEVICE_OBSERVATION,
        now=datetime.now(UTC),
    )


def test_missing_driver_facts_and_offline_device_cannot_claim_ready():
    key = DeviceObservationSigningKey(MemorySecretStore())
    authority = Authority(key)
    device = discovered_device()
    devices = Devices(device)
    reader = LiveDeviceObservationReader(
        devices=devices, authority=authority, signing_key=key
    )
    assert (
        reader.read_current(device.id).observation.reason == "driver_facts_unavailable"
    )

    devices.device = device.with_connection_state(DeviceConnectionState.OFFLINE)
    signed = reader.read_current(device.id)
    assert not signed.observation.ready
    assert signed.observation.reason == "device_offline"


def test_untrusted_observation_key_cannot_publish():
    key = DeviceObservationSigningKey(MemorySecretStore())
    authority = Authority(DeviceObservationSigningKey(MemorySecretStore()))
    device = discovered_device()
    reader = LiveDeviceObservationReader(
        devices=Devices(device), authority=authority, signing_key=key
    )
    assert reader.read_current(device.id) is None


def test_observation_key_survives_a_new_service_instance():
    secrets = MemorySecretStore()
    first = DeviceObservationSigningKey(secrets)
    second = DeviceObservationSigningKey(secrets)
    assert first.key_id() == second.key_id()
    assert first.public_key() == second.public_key()


def test_observation_key_command_exports_only_the_public_key(monkeypatch, capsys):
    from inari.commands import authority as command

    secrets = MemorySecretStore()
    monkeypatch.setattr(command, "load_settings", lambda config_path: object())
    monkeypatch.setattr(command, "build_secret_store", lambda settings: secrets)

    command.run_observation_key(None)
    first = json.loads(capsys.readouterr().out)
    command.run_observation_key(None)
    second = json.loads(capsys.readouterr().out)

    assert first == second
    assert first == {
        "key_id": DeviceObservationSigningKey(secrets).key_id(),
        "public_key": DeviceObservationSigningKey(secrets).public_key().hex(),
    }
    assert "private_key" not in first
