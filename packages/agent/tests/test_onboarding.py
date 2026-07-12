from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

import httpx
import pytest

from inari.config import AgentSettings
from inari.drivers import DeviceKind
from inari.gateway.models import (
    UpstreamCertificateMode,
    UpstreamConnectionState,
    UpstreamStatus,
)
from inari.gateway.onboarding import ManagedOnboardingService
from inari.gateway.service import GatewaySnapshotBuilder
from inari.runtime.models import DeviceConnectionState, DeviceRecord, utc_now
from inari.security.certificates.store import ManagedCertificate
from inari.security.models import (
    AgentIdentity,
    GatewayExposure,
    GatewayMode,
    GatewaySecurityPolicy,
)
from inari.security.secrets import MemorySecretStore

INVITE_ID = "ABCDEFGH2345"
INVITE_CODE = f"INR-{INVITE_ID}-ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"


@dataclass
class StubGatewayService:
    settings: AgentSettings

    def get_identity(self) -> AgentIdentity:
        return AgentIdentity(
            agent_id="agt_test",
            key_id="kid_test",
            algorithm="Ed25519",
            public_jwk={"kid": "kid_test"},
            created_at=utc_now(),
        )

    def get_upstream_status(self) -> UpstreamStatus:
        return UpstreamStatus(
            mode=self.settings.gateway_mode,
            state=(
                UpstreamConnectionState.DISCONNECTED
                if self.settings.gateway_mode is GatewayMode.MANAGED
                else UpstreamConnectionState.DISABLED
            ),
            base_url=self.settings.upstream_base_url,
        )


@dataclass
class StubDeviceCatalog:
    devices: tuple[DeviceRecord, ...] = ()

    def list_devices(self) -> tuple[DeviceRecord, ...]:
        return self.devices


@dataclass
class StubIdentitySource:
    identity: AgentIdentity

    def get_or_create_identity(self) -> AgentIdentity:
        return self.identity


class EmptyQueueMetrics:
    def queue_counts(self) -> dict[str, int]:
        return {}


class EmptyGatewayMetrics:
    def summary(self) -> dict[str, int]:
        return {}


@dataclass
class StaticSecurityPolicy:
    policy: GatewaySecurityPolicy


class EmptyCertificateSource:
    def current_certificate(self) -> ManagedCertificate | None:
        return None


def preview_client_factory(**kwargs):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(f"/{INVITE_ID}")
        return httpx.Response(
            200,
            json={
                "invitation_id": INVITE_ID,
                "expires_at": "2026-07-08T12:00:00Z",
                "state": "created",
                "controller_name": "Inari Production",
                "controller_instance_id": "controller-1",
                "supported_protocol_versions": ["2026-07-11"],
                "certificate_mode": "step_ca",
                "requires_mutual_tls_after_issuance": True,
            },
        )

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def onboarding_service(tmp_path, *, devices=()):
    config_path = tmp_path / "inari.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    settings = AgentSettings(
        resolved_config_path=config_path,
        security_state_dir=tmp_path / "security",
    )
    secret_store = MemorySecretStore()
    service = ManagedOnboardingService(
        settings=settings,
        secret_store=secret_store,
        gateway_service=StubGatewayService(settings),
        device_catalog=StubDeviceCatalog(tuple(devices)),
        status_path=tmp_path / "security" / "onboarding.json",
        http_client_factory=preview_client_factory,
    )
    return service, settings, secret_store, config_path


def test_invitation_parser_accepts_setup_links_deep_links_and_manual_codes(
    tmp_path,
) -> None:
    service, _, _, _ = onboarding_service(tmp_path)

    setup = service.parse_invitation(
        f"https://controller.example.com/setup/{INVITE_ID}#code={INVITE_CODE}"
    )
    deep_link = service.parse_invitation(
        "inari://enroll"
        f"?controller=https%3A%2F%2Fcontroller.example.com&invite_id={INVITE_ID}"
        f"#code={INVITE_CODE}"
    )
    manual = service.parse_invitation(
        INVITE_CODE, controller_url="https://controller.example.com"
    )

    assert setup == deep_link == manual


@pytest.mark.anyio
async def test_start_writes_secure_overlay_and_keeps_invite_only_in_secret_store(
    tmp_path,
) -> None:
    service, _, secret_store, config_path = onboarding_service(tmp_path)

    preview, restart_required = await service.start(
        INVITE_CODE,
        controller_url="https://controller.example.com",
    )

    overlay_path = config_path.with_name("inari.local.toml")
    overlay_text = overlay_path.read_text(encoding="utf-8")
    with overlay_path.open("rb") as handle:
        overlay = tomllib.load(handle)
    assert preview.controller_name == "Inari Production"
    assert restart_required is True
    assert overlay["agent"]["mode"] == "managed"
    assert overlay["controller"]["base_url"] == "https://controller.example.com"
    assert overlay["certificates"]["provider"] == "step_ca"
    assert INVITE_CODE not in overlay_text
    assert secret_store.get_secret("upstream_enrollment_token") == INVITE_CODE
    if os.name == "posix":
        assert overlay_path.stat().st_mode & 0o777 == 0o600


def test_device_confirmation_persists_labels_and_default_printer(tmp_path) -> None:
    now = utc_now()
    printer = DeviceRecord(
        id="dev_printer",
        kind=DeviceKind.PRINTER,
        driver_key="test.printer",
        name="System Printer",
        connection_state=DeviceConnectionState.ONLINE,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
        capabilities={"text": True},
    )
    service, settings, _, config_path = onboarding_service(tmp_path, devices=(printer,))

    service.confirm_devices(
        device_ids=(printer.id,),
        labels={printer.id: "Front counter"},
        default_printer_device_id=printer.id,
    )

    with config_path.with_name("inari.local.toml").open("rb") as handle:
        overlay = tomllib.load(handle)
    assert overlay["devices"]["labels"] == {printer.id: "Front counter"}
    assert overlay["devices"]["printing"]["default_printer"] == "System Printer"
    assert settings.device_labels == {printer.id: "Front counter"}
    assert settings.default_printer_name == "System Printer"


def test_gateway_snapshot_contains_redacted_full_device_inventory(tmp_path) -> None:
    now = utc_now()
    printer = DeviceRecord(
        id="dev_printer",
        kind=DeviceKind.PRINTER,
        driver_key="test.printer",
        name="System Printer",
        connection_state=DeviceConnectionState.ONLINE,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
        capabilities={"text": True, "cash_drawer": False},
        metadata={
            "device_class": "physical",
            "location": "Front desk",
            "access_token": "must-not-leave-device",
        },
    )
    settings = AgentSettings(
        security_state_dir=tmp_path / "security",
        device_labels={printer.id: "Front counter"},
    )
    builder = GatewaySnapshotBuilder(
        settings=settings,
        identity_service=StubIdentitySource(
            AgentIdentity(
                agent_id="agt_test",
                key_id="kid_test",
                algorithm="Ed25519",
                public_jwk={"kid": "kid_test"},
                created_at=now,
            )
        ),
        device_catalog=StubDeviceCatalog((printer,)),
        job_service=EmptyQueueMetrics(),
        gateway_repository=EmptyGatewayMetrics(),
        security_policy_service=StaticSecurityPolicy(
            GatewaySecurityPolicy(
                mode=GatewayMode.STANDALONE,
                exposure=GatewayExposure.LOOPBACK,
            )
        ),
        certificate_service=EmptyCertificateSource(),
        certificate_lifecycle_manager=None,
    )

    snapshot = builder.build_snapshot()
    inventory = snapshot.runtime.inventory.devices

    assert len(inventory) == 1
    assert inventory[0].display_name == "Front counter"
    assert inventory[0].system_name == "System Printer"
    assert inventory[0].capabilities == ("text",)
    assert inventory[0].metadata["location"] == "Front desk"
    assert "access_token" not in inventory[0].metadata
    assert snapshot.security.certificate_mode is UpstreamCertificateMode.CONTROLLER
