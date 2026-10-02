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


def _write_json(destination: Path, value: object) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_contracts(canonical_destination: Path, codegen_destination: Path) -> None:
    """Write the canonical contract and its OpenAPI 3.0 codegen projection."""

    canonical = app.openapi()
    _write_json(canonical_destination, canonical)
    codegen = _openapi_30(canonical)
    if not isinstance(codegen, dict):  # pragma: no cover - FastAPI contract invariant
        raise TypeError("FastAPI returned a non-object OpenAPI contract")
    codegen["openapi"] = "3.0.3"
    codegen.pop("jsonSchemaDialect", None)
    _write_json(codegen_destination, codegen)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the Inari local-agent OpenAPI contract."
    )
    parser.add_argument("canonical_destination", type=Path)
    parser.add_argument("codegen_destination", type=Path)
    arguments = parser.parse_args()
    write_contracts(
        arguments.canonical_destination,
        arguments.codegen_destination,
    )


if __name__ == "__main__":
    main()
