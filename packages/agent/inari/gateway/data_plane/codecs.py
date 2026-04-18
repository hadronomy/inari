from __future__ import annotations

import json
from typing import Any, TypeVar

T = TypeVar("T")


def dump_json_payload(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def load_json_payload(payload: str) -> Any:
    return json.loads(payload)
