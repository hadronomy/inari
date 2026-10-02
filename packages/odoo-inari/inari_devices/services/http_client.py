from __future__ import annotations

import json
import time
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.auth import AuthBase


class RemoteServiceError(RuntimeError):
    """A content-free failure that preserves only the remote HTTP status."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise RemoteServiceError("The service URL is invalid.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise RemoteServiceError("The service URL must use HTTPS without credentials.")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", authority, parsed.path, "", ""))


def https_origin(value: str) -> str:
    normalized = https_url(value)
    if urlsplit(normalized).path not in {"", "/"}:
        raise RemoteServiceError("The service address must be an exact HTTPS origin.")
    return normalized.rstrip("/")


class JsonHttpClient:
    """Keep credentials on one HTTPS origin and bound all response bodies."""

    def __init__(
        self,
        address: str,
        *,
        session: requests.Session | None = None,
        ca_certificate: str | bool = True,
        client_certificate: tuple[str, str] | None = None,
        timeout_seconds: float = 5,
        response_limit: int = 256 * 1024,
    ) -> None:
        self.address = https_origin(address)
        valid_ca = ca_certificate is True or (
            isinstance(ca_certificate, str) and bool(ca_certificate.strip())
        )
        if not valid_ca or timeout_seconds <= 0 or response_limit <= 0:
            raise RemoteServiceError("The service transport configuration is invalid.")
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._verify = ca_certificate
        self._certificate = client_certificate
        self._timeout = timeout_seconds
        self._limit = response_limit

    def close(self) -> None:
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        data: str | None = None,
        auth: AuthBase | None = None,
    ) -> Mapping[str, object]:
        if not path.startswith("/") or path.startswith("//") or "#" in path:
            raise RemoteServiceError("The service request path is invalid.")
        deadline = time.monotonic() + self._timeout
        try:
            with self._session.request(
                method,
                self.address + path,
                headers={"Accept": "application/json", **(headers or {})},
                json=json_body,
                data=data,
                auth=auth,
                timeout=self._timeout,
                verify=self._verify,
                cert=self._certificate,
                allow_redirects=False,
                stream=True,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise RemoteServiceError(
                        "The service rejected the request.", status=response.status_code
                    )
                content = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    if len(content) + len(chunk) > self._limit:
                        raise RemoteServiceError(
                            "The service response exceeds its limit."
                        )
                    if time.monotonic() > deadline:
                        raise RemoteServiceError("The service response timed out.")
                    content.extend(chunk)
                document = json.loads(content)
        except requests.RequestException:
            raise RemoteServiceError("The service is not available.") from None
        except (ValueError, UnicodeError):
            raise RemoteServiceError("The service returned invalid JSON.") from None
        if not isinstance(document, dict):
            raise RemoteServiceError("The service returned an invalid response.")
        return document
