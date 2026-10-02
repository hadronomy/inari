from __future__ import annotations

import base64
from types import MappingProxyType
from typing import Mapping

from ..device_authority import canonical_json_bytes
from ..security.identity import AgentIdentityService


_JWS_TYPE = "inari-agent-event+jws"


class AgentEventSigner:
    """Sign canonical Device Stream messages with the Agent Identity."""

    def __init__(self, identity_service: AgentIdentityService) -> None:
        self._identity_service = identity_service
        identity = identity_service.get_or_create_identity()
        self._agent_id = identity.agent_id
        self._key_id = identity.key_id
        self._public_jwk = MappingProxyType(dict(identity.public_jwk))

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_jwk(self) -> Mapping[str, str]:
        return self._public_jwk

    def sign(self, document: Mapping[str, object]) -> str:
        protected = _base64url(
            canonical_json_bytes(
                {"alg": "EdDSA", "kid": self._key_id, "typ": _JWS_TYPE}
            )
        )
        payload = _base64url(canonical_json_bytes(document))
        signing_input = f"{protected}.{payload}".encode("ascii")
        signature = _base64url(self._identity_service.sign(signing_input))
        return f"{protected}.{payload}.{signature}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


__all__ = ["AgentEventSigner"]
