from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import (
    CapabilityAdmissionTarget,
    DeviceCapability,
    SignedAuthorityRevision,
    SignedBindingRevision,
    SignedDeviceObservation,
    SignedDriverProfile,
    SignedHardwareCertificationMatrixRow,
)


@dataclass(frozen=True, slots=True)
class DeviceTestAuthorization:
    """The checked graph for standard, non-business Device Work before activation."""

    target: CapabilityAdmissionTarget
    revision: SignedAuthorityRevision
    binding: SignedBindingRevision
    profile: SignedDriverProfile
    certification: SignedHardwareCertificationMatrixRow
    capability: DeviceCapability
    observation: SignedDeviceObservation
    issued_at: datetime
    expires_at: datetime


_ISSUER = object()


class DeviceTestPermit:
    """An opaque Device Test permit that cannot supply business authority proof."""

    __slots__ = ("__authorization", "__token")

    def __init__(
        self,
        issuer: object,
        authorization: DeviceTestAuthorization,
        token: bytes,
    ) -> None:
        if issuer is not _ISSUER:
            raise TypeError("Device Test permits are issued by the authority")
        if len(token) != 32:
            raise ValueError("The Device Test permit token must contain 32 bytes")
        object.__setattr__(self, "_DeviceTestPermit__authorization", authorization)
        object.__setattr__(self, "_DeviceTestPermit__token", token)

    @classmethod
    def _issue(
        cls, authorization: DeviceTestAuthorization, token: bytes
    ) -> DeviceTestPermit:
        return cls(_ISSUER, authorization, token)

    @property
    def authorization(self) -> DeviceTestAuthorization:
        return self.__authorization

    def _token_for_test_check(self) -> bytes:
        return self.__token

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Device Test permits are immutable")
