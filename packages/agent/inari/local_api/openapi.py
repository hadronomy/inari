from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .app import app


def _openapi_30(value: Any) -> Any:
    """Translate Pydantic's JSON Schema vocabulary into OpenAPI 3.0."""
    if isinstance(value, list):
        return [_openapi_30(item) for item in value]
    if not isinstance(value, dict):
        return value

    schema = {key: _openapi_30(item) for key, item in value.items()}

    if "const" in schema:
        schema["enum"] = [schema.pop("const")]

    for bound in ("Minimum", "Maximum"):
        exclusive = f"exclusive{bound}"
        numeric = schema.get(exclusive)
        if isinstance(numeric, int | float) and not isinstance(numeric, bool):
            schema[bound.lower()] = numeric
            schema[exclusive] = True

    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        concrete = [
            alternative
            for alternative in alternatives
            if not (isinstance(alternative, dict) and alternative.get("type") == "null")
        ]
        if len(concrete) != len(alternatives):
            schema["nullable"] = True
            if len(concrete) == 1:
                schema.pop("anyOf")
                schema.update(concrete[0])
            else:
                schema["anyOf"] = concrete

    return schema


def write_schema(destination: Path) -> None:
    """Write the local-agent contract in a deterministic representation."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(_openapi_30(app.openapi()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the Inari local-agent OpenAPI contract."
    )
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    write_schema(arguments.destination)


if __name__ == "__main__":
    main()
