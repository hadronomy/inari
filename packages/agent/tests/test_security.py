from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iot_agent.config import AgentSettings
from iot_agent.security.identity import AgentIdentityService
from iot_agent.security.policies import SecurityPolicyService
from iot_agent.security.secrets import MemorySecretStore
from iot_agent.security.tokens import TokenService
from iot_agent.security.models import AccessScope, GatewayExposure


class AgentIdentityServiceTests(unittest.TestCase):
    def test_identity_is_stable_across_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identity_path = Path(temp_dir) / "identity.pem"
            first_service = AgentIdentityService(identity_path=identity_path)
            second_service = AgentIdentityService(identity_path=identity_path)

            first = first_service.get_or_create_identity()
            second = second_service.get_or_create_identity()

            self.assertEqual(first.agent_id, second.agent_id)
            self.assertEqual(first.key_id, second.key_id)
            self.assertEqual(first.public_jwk, second.public_jwk)


class TokenServiceTests(unittest.TestCase):
    def test_token_service_round_trips_local_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identity_service = AgentIdentityService(identity_path=Path(temp_dir) / "identity.pem")
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

            self.assertEqual(principal.subject, "local:tray")
            self.assertTrue(principal.has_scopes((AccessScope.SYSTEM_READ,)))
            self.assertFalse(principal.has_scopes((AccessScope.ADMIN_WRITE,)))


class SecurityPolicyServiceTests(unittest.TestCase):
    def test_loopback_settings_validate_cleanly(self) -> None:
        settings = AgentSettings()

        SecurityPolicyService(settings).validate_startup()

    def test_lan_settings_require_tls(self) -> None:
        settings = AgentSettings(host="0.0.0.0", gateway_exposure=GatewayExposure.LAN)

        with self.assertRaises(RuntimeError):
            SecurityPolicyService(settings).validate_startup()


if __name__ == "__main__":
    unittest.main()
