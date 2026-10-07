from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import os
import re
from urllib.parse import urlsplit, urlunsplit

from .http_client import JsonHttpClient, RemoteServiceError, https_url
from .openbao import build_openbao_client
from .workload_identity import WorkloadScope, WorkloadTokenProvider


_API = "/api/inari/v1/managed-work"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_STATES = {
    "pending_agent",
    "dispatching",
    "accepted",
    "rejected",
    "canceled",
    "expired",
    "recovery_uncertain",
}
_MEDIA_TYPES = {
    "report_pdf": "application/pdf",
    "label_document": "application/vnd.zebra-zpl",
}


def identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise RemoteServiceError("The Managed Work identity is invalid.")
    return value


def utc_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise RemoteServiceError("The Managed Work timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise RemoteServiceError("The Managed Work timestamp is invalid.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RemoteServiceError("The Managed Work timestamp has no timezone.")
    return parsed


class ManagedWorkClient:
    def __init__(self, http: JsonHttpClient, tokens: WorkloadTokenProvider) -> None:
        self._http = http
        self._tokens = tokens
        self.scope = tokens.scope

    def __enter__(self) -> ManagedWorkClient:
        return self

    def __exit__(self, *_exc) -> None:
        self._http.close()
        self._tokens.close()

    def _request(self, method: str, path: str, *, body=None, idempotency_key=None):
        headers = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = identifier(idempotency_key)
        for attempt in range(2):
            headers["Authorization"] = f"Bearer {self._tokens.token()}"
            try:
                return self._http.request(method, path, headers=headers, json_body=body)
            except RemoteServiceError as error:
                if error.status != 401 or attempt:
                    raise
                self._tokens.invalidate()
        raise AssertionError("authentication retry did not return")

    def inventory(self):
        from .inventory import inventory

        return inventory(
            self._request("GET", "/api/inari/v1/workload/inventory"), self.scope
        )

    def preflight(self, request: Mapping[str, object]) -> Mapping[str, object]:
        if not self.scope.matches(request.get("scope")):
            raise RemoteServiceError(
                "Managed Work does not belong to this company identity."
            )
        operation = request.get("operation")
        if (
            not isinstance(operation, str)
            or operation not in _MEDIA_TYPES
            or request.get("contract_major") != 1
        ):
            raise RemoteServiceError("The Managed Work contract is not supported.")
        result = self._request("POST", _API + "/preflight", body=request)
        if (
            type(result.get("contract_major")) is not int
            or result["contract_major"] != 1
            or result.get("operation") != operation
            or result.get("device_id") != request.get("device_id")
            or result.get("media_type") != _MEDIA_TYPES[operation]
            or result.get("state") not in ("ready", "blocked")
        ):
            raise RemoteServiceError(
                "Controller preflight does not match the requested Device Work."
            )
        if result["state"] == "ready":
            identifier(result.get("preflight_id"))
            identifier(result.get("capability_digest"))
            expires_at = utc_datetime(result.get("expires_at"))
            if (
                not utc_datetime(result.get("submit_before"))
                <= expires_at
                < utc_datetime(result.get("idempotency_expires_at"))
            ):
                raise RemoteServiceError("Controller preflight deadlines are invalid.")
        return result

    def submit(
        self, idempotency_key: str, submission: Mapping[str, object]
    ) -> Mapping[str, object]:
        work = submission.get("work")
        if not isinstance(work, dict) or not self.scope.matches(work.get("scope")):
            raise RemoteServiceError(
                "Managed Work does not belong to this company identity."
            )
        receipt = self._request(
            "POST", _API, body=submission, idempotency_key=idempotency_key
        )
        identifier(receipt.get("managed_work_id"))
        if not isinstance(receipt.get("state"), str) or receipt["state"] not in _STATES:
            raise RemoteServiceError(
                "The Controller returned an invalid Managed Work receipt."
            )
        return receipt

    def get(self, managed_work_id: str) -> Mapping[str, object]:
        record = self._request("GET", _API + "/" + identifier(managed_work_id))
        if record.get("managed_work_id") != managed_work_id:
            raise RemoteServiceError(
                "The Controller returned another Managed Work identity."
            )
        return self._record(record)

    def find(self, idempotency_key: str) -> Mapping[str, object] | None:
        try:
            record = self._request(
                "GET", _API + "/by-idempotency-key", idempotency_key=idempotency_key
            )
        except RemoteServiceError as error:
            if error.status == 404:
                return None
            raise
        return self._record(record)

    def _record(self, record: Mapping[str, object]) -> Mapping[str, object]:
        if not self.scope.matches(record.get("scope")):
            raise RemoteServiceError("The Controller returned another company scope.")
        identifier(record.get("managed_work_id"))
        identifier(record.get("print_intent_id"))
        identifier(record.get("device_id"))
        if not isinstance(record.get("state"), str) or record["state"] not in _STATES:
            raise RemoteServiceError(
                "The Controller returned an invalid Managed Work state."
            )
        utc_datetime(record.get("expires_at"))
        return record


def build_managed_work_client(
    *,
    database: str,
    company_id: int,
    organization_id: str,
    client_id: str,
    issuer: str,
    controller: str,
) -> ManagedWorkClient:
    scope = WorkloadScope(database, str(company_id), organization_id)
    issuer_parts = urlsplit(https_url(issuer))
    openbao, tokens = build_openbao_client()
    return ManagedWorkClient(
        JsonHttpClient(
            controller, ca_certificate=os.environ.get("INARI_CONTROLLER_CACERT", True),
            response_limit=2 * 1024 * 1024,
        ),
        WorkloadTokenProvider(
            scope=scope,
            issuer=issuer,
            client_id=client_id,
            openbao=openbao,
            openbao_tokens=tokens,
            http=JsonHttpClient(
                urlunsplit(("https", issuer_parts.netloc, "", "", "")),
                ca_certificate=os.environ.get("INARI_OIDC_CACERT", True),
                response_limit=65536,
            ),
            credential_mount=os.environ.get("INARI_OPENBAO_CREDENTIAL_MOUNT", "secret"),
        ),
    )
