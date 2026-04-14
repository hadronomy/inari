from __future__ import annotations

from pathlib import Path

import pytest

from iot_agent.config import AgentSettings
from iot_agent.security.identity import AgentIdentityService
from iot_agent.security.models import AccessScope, GatewayExposure
from iot_agent.security.policies import SecurityPolicyService
from iot_agent.security.secrets import MemorySecretStore
from iot_agent.security.tokens import TokenService


def test_identity_is_stable_across_reloads(tmp_path: Path) -> None:
    identity_path = tmp_path / "identity.pem"
    first_service = AgentIdentityService(identity_path=identity_path)
    second_service = AgentIdentityService(identity_path=identity_path)

    first = first_service.get_or_create_identity()
    second = second_service.get_or_create_identity()

    assert first.agent_id == second.agent_id
    assert first.key_id == second.key_id
    assert first.public_jwk == second.public_jwk


def test_token_service_round_trips_local_token(tmp_path: Path) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    token_service = TokenService(
        secret_store=MemorySecretStore(),
        identity_service=identity_service,
        token_ttl_seconds=3600,
        token_audience="iot-agent.local",
    )

    token = token_service.issue_local_token(
        client_name="tray",
        scopes=(AccessScope.SYSTEM_READ, AccessScope.EVENTS_READ),
    )
    principal = token_service.authenticate_token(token.access_token)

    assert principal.subject == "local:tray"
    assert principal.has_scopes((AccessScope.SYSTEM_READ,))
    assert not principal.has_scopes((AccessScope.ADMIN_WRITE,))


def test_loopback_settings_validate_cleanly() -> None:
    settings = AgentSettings()

    SecurityPolicyService(settings).validate_startup()


def test_lan_settings_require_tls() -> None:
    settings = AgentSettings(host="0.0.0.0", gateway_exposure=GatewayExposure.LAN)

    with pytest.raises(RuntimeError):
        SecurityPolicyService(settings).validate_startup()
