from __future__ import annotations

import sys
from logging.handlers import RotatingFileHandler

from inari.core.logging import configure_logging


def test_logging_uses_only_the_file_handler_without_a_console(
    mocker, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(sys, "stderr", None)
    root = mocker.patch("inari.core.logging.logging.getLogger")
    root.return_value.handlers = []

    configure_logging(log_dir=tmp_path)

    handlers = [call.args[0] for call in root.return_value.addHandler.call_args_list]
    assert len(handlers) == 1
    assert isinstance(handlers[0], RotatingFileHandler)
    for handler in handlers:
        handler.close()
