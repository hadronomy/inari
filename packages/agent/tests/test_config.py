from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from iot_agent.config import AgentSettings


class AgentSettingsTests(unittest.TestCase):
    def test_settings_read_iot_agent_prefixed_environment_variables(self) -> None:
        with patch.dict(
            os.environ,
            {
                "IOT_AGENT_LOG_LEVEL": "DEBUG",
                "IOT_AGENT_DEFAULT_PRINTER_NAME": "Kitchen Printer",
                "IOT_AGENT_GATEWAY_MODE": "managed",
                "IOT_AGENT_GATEWAY_EXPOSURE": "loopback",
                "IOT_AGENT_TRUSTED_HOSTS": "[\"127.0.0.1\", \"localhost\"]",
            },
            clear=False,
        ):
            settings = AgentSettings()

        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.default_printer_name, "Kitchen Printer")
        self.assertEqual(settings.gateway_mode.value, "managed")
        self.assertEqual(settings.gateway_exposure.value, "loopback")
        self.assertEqual(settings.trusted_hosts, ["127.0.0.1", "localhost"])


if __name__ == "__main__":
    unittest.main()
