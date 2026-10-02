from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol

from ..device_authority import AdmissionPermit, CapabilityStreamTarget


class StreamAuthority(Protocol):
    def authorize_stream(
        self,
        target: CapabilityStreamTarget,
        *,
        now: datetime | None = None,
    ) -> AdmissionPermit: ...

    def check(
        self,
        permit: AdmissionPermit,
        *,
        now: datetime | None = None,
    ) -> object: ...


class DeviceStreamLedger(Protocol):
    def current_sequence(self) -> int: ...

    def next_sequence(self) -> int: ...

    def next_generation(self, scope_digest: str) -> int: ...


class StreamSigner(Protocol):
    @property
    def agent_id(self) -> str: ...

    @property
    def key_id(self) -> str: ...

    @property
    def public_jwk(self) -> Mapping[str, str]: ...

    def sign(self, document: Mapping[str, object]) -> str: ...


__all__ = ["DeviceStreamLedger", "StreamAuthority", "StreamSigner"]
