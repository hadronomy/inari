from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any, cast

from inari.config import AgentSettings
from inari.local_api.server import AgentServerController


def test_server_config_supports_a_process_without_console_streams(
    mocker, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    mocker.patch(
        "inari.local_api.server.create_app",
        return_value=mocker.AsyncMock(),
    )
    container = cast(Any, SimpleNamespace(tls_context_factory=None))

    controller = AgentServerController.from_settings(
        AgentSettings(),
        container=container,
    )

    assert controller.server.config.log_config is None
