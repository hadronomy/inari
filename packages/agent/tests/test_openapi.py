from __future__ import annotations

import json
from pathlib import Path

from inari.local_api.openapi import write_contracts


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_contracts_keep_openapi_31_canonical_and_openapi_30_for_codegen(
    tmp_path: Path,
) -> None:
    canonical_path = tmp_path / "local-agent.openapi.json"
    codegen_path = tmp_path / "local-agent.codegen.openapi.json"

    write_contracts(canonical_path, codegen_path)

    canonical = _load(canonical_path)
    codegen = _load(codegen_path)
    assert canonical["openapi"] == "3.1.0"
    assert codegen["openapi"] == "3.0.3"


def test_contract_exposes_only_the_explicit_device_work_submission(
    tmp_path: Path,
) -> None:
    canonical_path = tmp_path / "local-agent.openapi.json"
    codegen_path = tmp_path / "local-agent.codegen.openapi.json"

    write_contracts(canonical_path, codegen_path)

    for contract in (_load(canonical_path), _load(codegen_path)):
        paths = contract["paths"]
        assert isinstance(paths, dict)
        assert "/v1/device-work" in paths
        assert "/v1/jobs/query" in paths
        assert "/print-jobs" not in paths
        operation = paths["/v1/device-work"]
        assert isinstance(operation, dict)
        request_body = operation["post"]["requestBody"]
        assert "multipart/form-data" in request_body["content"]
        challenge_headers = operation["post"]["responses"]["401"]["headers"]
        assert set(challenge_headers) == {
            "Cache-Control",
            "DPoP-Nonce",
            "WWW-Authenticate",
        }
        query_operation = paths["/v1/jobs/query"]["post"]
        query_schema = query_operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        assert query_schema["$ref"].endswith("/PrintJobQueryRequest")


def test_contract_exposes_the_separate_client_pairing_interface(
    tmp_path: Path,
) -> None:
    canonical_path = tmp_path / "local-agent.openapi.json"
    codegen_path = tmp_path / "local-agent.codegen.openapi.json"

    write_contracts(canonical_path, codegen_path)

    expected = {
        "/pairing/v1/requests",
        "/pairing/v1/requests/{request_id}",
        "/pairing/v1/requests/{request_id}/admit",
        "/pairing/v1/requests/{request_id}/cancel",
        "/pairing/v1/requests/{request_id}/review",
        "/pairing/v1/requests/{request_id}/decision",
        "/pairing/v1/client-grants/renew",
    }
    for contract in (_load(canonical_path), _load(codegen_path)):
        paths = contract["paths"]
        assert isinstance(paths, dict)
        assert expected <= paths.keys()
        create = paths["/pairing/v1/requests"]["post"]
        assert create["operationId"] == "create_client_pairing_request"
        assert set(create["responses"]["401"]["headers"]) == {
            "Cache-Control",
            "DPoP-Nonce",
            "WWW-Authenticate",
        }
