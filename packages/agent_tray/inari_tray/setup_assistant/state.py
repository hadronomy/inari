from __future__ import annotations

from enum import StrEnum

from inari.local_api.schemas import ManagedOnboardingStatusResponse


class SetupAccess(StrEnum):
    UNKNOWN = "unknown"
    REQUIRED = "required"
    COMPLETE = "complete"


class SetupIntent(StrEnum):
    BACKGROUND = "background"
    FOREGROUND = "foreground"


class SetupStep(StrEnum):
    INVITATION = "invitation"
    CHECKING = "checking"
    SECURING = "securing"
    CONNECTING = "connecting"
    DEVICES = "devices"
    READY = "ready"
    FAILED = "failed"


def access_for_status(status: ManagedOnboardingStatusResponse) -> SetupAccess:
    return (
        SetupAccess.COMPLETE
        if status.completed_at is not None
        else SetupAccess.REQUIRED
    )


def step_for_status(status: ManagedOnboardingStatusResponse) -> SetupStep:
    if status.completed_at is not None:
        return SetupStep.READY
    match status.phase:
        case "restart_required" | "securing_connection":
            return SetupStep.SECURING
        case "connecting":
            return SetupStep.CONNECTING
        case "finding_devices":
            return SetupStep.DEVICES
        case "ready":
            return SetupStep.DEVICES
        case "failed":
            return SetupStep.FAILED
        case "not_started":
            return SetupStep.INVITATION
        case _:
            return SetupStep.CHECKING
