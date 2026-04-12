from __future__ import annotations

import runpy
import unittest
from unittest.mock import patch


class EntryPointTests(unittest.TestCase):
    def test_package_entrypoint_invokes_main(self) -> None:
        with patch("iot_agent.main.main") as mocked_main:
            runpy.run_module("iot_agent", run_name="__main__")

        mocked_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
