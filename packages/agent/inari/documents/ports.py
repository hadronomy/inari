from __future__ import annotations

from typing import Protocol

from ..device_authority import AdmissionAuthorizer

from .models import (
    AdmissionAccepted,
    AdmissionRequest,
    DurableAdmission,
)


class DocumentAdmission(Protocol):
    async def admit(self, request: AdmissionRequest) -> AdmissionAccepted:
        """Validate and durably accept one document work item."""


class DocumentAdmissionStore(Protocol):
    async def accept(self, admission: DurableAdmission) -> AdmissionAccepted:
        """Persist an accepted work item and return its stable identifiers."""


__all__ = ["AdmissionAuthorizer", "DocumentAdmission", "DocumentAdmissionStore"]
