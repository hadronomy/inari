from __future__ import annotations

import json
from pathlib import Path

from .support.device_authority_vectors import vectors


def test_authority_vectors_match_the_agent_protocol() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts/device-authority.test-vectors.json"
    )
    assert json.loads(path.read_text()) == vectors()
