from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ...gateway.models import UpstreamCertificateMode
from .base import APIModel
from .devices import DeviceResponse

if TYPE_CHECKING:
    from ...gateway.onboarding import (
        OnboardingControllerPreview,
        OnboardingStatus,
    )

OnboardingPhaseValue = Literal[
    "not_started",
    "restart_required",
    "securing_connection",
    "connecting",
    "finding_devices",
    "ready",
    "failed",
]


class ManagedOnboardingInvitationRequest(APIModel):
    invitation: str = Field(min_length=1, max_length=2048)
    controller_url: str | None = Field(default=None, max_length=2048)


class ManagedOnboardingPreviewResponse(APIModel):
    invite_id: str
    controller_url: str
    controller_name: str | None = None
    controller_instance_id: str | None = None
    expires_at: datetime
    status: str
    supported_protocol_versions: tuple[str, ...] = ()
    certificate_mode: UpstreamCertificateMode
    requires_mutual_tls_after_issuance: bool

    @classmethod
    def from_domain(
        cls, preview: OnboardingControllerPreview
    ) -> ManagedOnboardingPreviewResponse:
        return cls(
            invite_id=preview.invite_id,
            controller_url=preview.controller_url,
            controller_name=preview.controller_name,
            controller_instance_id=preview.controller_instance_id,
            expires_at=preview.expires_at,
            status=preview.status,
            supported_protocol_versions=preview.supported_protocol_versions,
            certificate_mode=preview.certificate_mode,
            requires_mutual_tls_after_issuance=(
                preview.requires_mutual_tls_after_issuance
            ),
        )


class ManagedOnboardingStartResponse(ManagedOnboardingPreviewResponse):
    restart_required: bool

    @classmethod
    def from_start(
        cls,
        preview: OnboardingControllerPreview,
        *,
        restart_required: bool,
    ) -> ManagedOnboardingStartResponse:
        return cls(
            **ManagedOnboardingPreviewResponse.from_domain(preview).model_dump(),
            restart_required=restart_required,
        )


class ManagedOnboardingStatusResponse(APIModel):
    phase: OnboardingPhaseValue
    detail: str
    restart_required: bool = False
    controller_url: str | None = None
    controller_name: str | None = None
    agent_id: str | None = None
    protocol_version: str | None = None
    zenoh_namespace: str | None = None
    certificate_expires_at: datetime | None = None
    devices: list[DeviceResponse] = Field(default_factory=list)
    last_error: str | None = None

    @classmethod
    def from_domain(
        cls,
        status: OnboardingStatus,
        *,
        devices: list[DeviceResponse],
    ) -> ManagedOnboardingStatusResponse:
        return cls(
            phase=status.phase.value,
            detail=status.detail,
            restart_required=status.restart_required,
            controller_url=status.controller_url,
            controller_name=status.controller_name,
            agent_id=status.agent_id,
            protocol_version=status.protocol_version,
            zenoh_namespace=status.zenoh_namespace,
            certificate_expires_at=status.certificate_expires_at,
            devices=devices,
            last_error=status.last_error,
        )


class ManagedOnboardingDeviceConfirmationRequest(APIModel):
    device_ids: tuple[str, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)
    default_printer_device_id: str | None = None
