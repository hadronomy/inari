from __future__ import annotations

from enum import StrEnum


class SetupStep(StrEnum):
    INVITATION = "invitation"
    CHECKING = "checking"
    SECURING = "securing"
    CONNECTING = "connecting"
    DEVICES = "devices"
    READY = "ready"
    FAILED = "failed"


def step_for_phase(phase: str, *, has_devices: bool) -> SetupStep:
    match phase:
        case "restart_required" | "securing_connection":
            return SetupStep.SECURING
        case "connecting":
            return SetupStep.CONNECTING
        case "finding_devices":
            return SetupStep.DEVICES if has_devices else SetupStep.CONNECTING
        case "ready":
            return SetupStep.DEVICES if has_devices else SetupStep.READY
        case "failed":
            return SetupStep.FAILED
        case _:
            return SetupStep.CHECKING
