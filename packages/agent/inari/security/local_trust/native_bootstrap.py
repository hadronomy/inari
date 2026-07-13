from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

WINDOWS_PAIRING_PIPE = r"\\.\pipe\Inari.Agent.Pairing"


class NativePairingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_secret: str
    expires_at: datetime
