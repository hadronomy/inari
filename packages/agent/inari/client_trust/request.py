"""Framework-free request normalization for browser Client Trust.

The request target comes from trusted ASGI connection fields.  Header values
can only confirm that target.  They cannot replace its scheme or authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import ipaddress
import re
from types import MappingProxyType
from typing import TypeAlias, cast
from urllib.parse import parse_qsl, quote, urlsplit


class RequestTargetError(ValueError):
    """The request contains an unsafe or ambiguous transport value."""


HeaderValue: TypeAlias = str | bytes
HeaderPair: TypeAlias = tuple[HeaderValue, HeaderValue]
HeaderInput: TypeAlias = Mapping[HeaderValue, HeaderValue] | Iterable[HeaderPair]
ServerAddress: TypeAlias = tuple[str, int]

_SECURITY_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "dpop",
        "forwarded",
        "host",
        "origin",
        "x-forwarded-host",
    }
)
_FORWARDED_HEADERS = frozenset({"forwarded", "x-forwarded-host"})
_QUERY_CREDENTIAL_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "dpop",
        "password",
        "secret",
        "session",
        "session_id",
        "token",
    }
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_METHOD = re.compile(r"^[A-Z][A-Z0-9-]{0,19}$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_PORT = re.compile(r"^[0-9]+$")
_PATH_SAFE = "/:@-._~!$&'()*+,;=%"


@dataclass(frozen=True, slots=True)
class NormalizedHeaders(Mapping[str, str]):
    """A case-insensitive header map with singleton security fields enforced."""

    _values: Mapping[str, str]

    def __getitem__(self, name: str) -> str:
        if not isinstance(name, str):
            raise KeyError(name)
        return self._values[name.casefold()]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass(frozen=True, slots=True)
class RequestTarget:
    """The canonical scheme, authority, and path used by DPoP `htu`."""

    method: str
    scheme: str
    authority: str
    path: str

    @property
    def htu(self) -> str:
        """Return the URI without query or fragment components."""

        return f"{self.scheme}://{self.authority}{self.path}"

    @classmethod
    def from_asgi_scope(cls, scope: Mapping[str, object]) -> RequestTarget:
        """Build a target from ASGI fields without trusting proxy headers."""

        try:
            scheme = cast(str, scope["scheme"])
            server = cast(ServerAddress, scope["server"])
            path = cast(str, scope["path"])
        except (KeyError, TypeError) as exc:
            raise RequestTargetError("The ASGI request target is incomplete.") from exc

        raw_path = scope.get("raw_path")
        headers = normalize_headers(cast(HeaderInput, scope.get("headers", ())))
        return build_request_target(
            method=cast(str, scope.get("method")),
            scheme=scheme,
            server=server,
            path=path,
            raw_path=cast(bytes | None, raw_path),
            headers=headers,
        )


@dataclass(frozen=True, slots=True)
class BrowserRequest:
    """The trusted request target and required browser transport headers."""

    target: RequestTarget
    origin: str
    authorization: str
    dpop: str


def normalize_headers(headers: HeaderInput | NormalizedHeaders) -> NormalizedHeaders:
    """Normalize header names and reject duplicate or folded security fields."""

    if isinstance(headers, NormalizedHeaders):
        return headers

    pairs: Iterable[HeaderPair]
    if isinstance(headers, Mapping):
        pairs = cast(Iterable[HeaderPair], headers.items())
    else:
        pairs = headers
    values: dict[str, str] = {}
    for raw_name, raw_value in pairs:
        name = _decode_header_part(raw_name, field="name", strip_ows=False)
        value = _decode_header_part(raw_value, field=name)
        normalized_name = name.casefold()
        if not _HEADER_NAME.fullmatch(name):
            raise RequestTargetError("A header name is invalid.")
        if normalized_name in values:
            if normalized_name in _SECURITY_HEADERS:
                raise RequestTargetError(
                    f"The {normalized_name} header must occur exactly once."
                )
            raise RequestTargetError(f"The {normalized_name} header is duplicated.")
        if normalized_name in _SECURITY_HEADERS and "," in value:
            raise RequestTargetError(
                f"The {normalized_name} header cannot use comma folding."
            )
        if normalized_name in _SECURITY_HEADERS and not value:
            raise RequestTargetError(f"The {normalized_name} header is empty.")
        values[normalized_name] = value

    if _FORWARDED_HEADERS.intersection(values):
        raise RequestTargetError("Forwarded host authority is not accepted.")
    return NormalizedHeaders(MappingProxyType(values))


def normalize_origin(value: str | bytes | None) -> str:
    """Normalize one exact HTTP(S) browser origin or reject it."""

    if value is None:
        raise RequestTargetError("The Origin header is required.")
    origin = _decode_header_part(value, field="origin")
    if origin.casefold() in {"null", "*"}:
        raise RequestTargetError("The Origin header is not an exact origin.")
    if not origin or any(char.isspace() for char in origin) or "," in origin:
        raise RequestTargetError("The Origin header is invalid.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in origin):
        raise RequestTargetError("The Origin header is invalid.")
    try:
        parsed = urlsplit(origin)
    except ValueError as exc:
        raise RequestTargetError("The Origin header is invalid.") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise RequestTargetError("The Origin header must use HTTP or HTTPS.")
    if parsed.path or parsed.query or parsed.fragment or "?" in origin or "#" in origin:
        raise RequestTargetError(
            "The Origin header cannot contain a path, query, or fragment."
        )
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise RequestTargetError("The Origin header cannot contain user information.")
    try:
        authority = _normalize_authority(parsed.netloc, scheme=scheme)
    except RequestTargetError:
        raise
    except (TypeError, ValueError) as exc:
        raise RequestTargetError("The Origin header authority is invalid.") from exc
    return f"{scheme}://{authority}"


def build_request_target(
    *,
    method: str,
    scheme: str,
    server: ServerAddress,
    path: str,
    raw_path: bytes | None = None,
    headers: HeaderInput | NormalizedHeaders = (),
) -> RequestTarget:
    """Build a trusted target and compare, but never trust, the Host header."""

    normalized_method = _normalize_method(method)
    normalized_scheme = _normalize_scheme(scheme)
    authority = _normalize_server(server, scheme=normalized_scheme)
    normalized_headers = normalize_headers(headers)
    host = normalized_headers.get("host")
    if (
        host is not None
        and _normalize_authority(host, scheme=normalized_scheme) != authority
    ):
        raise RequestTargetError(
            "The Host header does not match the connection authority."
        )
    normalized_path = _normalize_path(path, raw_path=raw_path)
    return RequestTarget(
        normalized_method, normalized_scheme, authority, normalized_path
    )


def build_browser_request(
    *,
    method: str,
    scheme: str,
    server: ServerAddress,
    path: str,
    query_string: str | bytes = b"",
    raw_path: bytes | None = None,
    headers: HeaderInput | NormalizedHeaders = (),
) -> BrowserRequest:
    """Build a browser request after applying all transport-level safeguards."""

    normalized_headers = normalize_headers(headers)
    target = build_request_target(
        method=method,
        scheme=scheme,
        server=server,
        path=path,
        raw_path=raw_path,
        headers=normalized_headers,
    )
    origin = normalize_origin(normalized_headers.get("origin"))
    authorization = _required_header(normalized_headers, "authorization")
    dpop = _required_header(normalized_headers, "dpop")
    _reject_browser_credentials(normalized_headers, query_string)
    return BrowserRequest(target, origin, authorization, dpop)


def _required_header(headers: NormalizedHeaders, name: str) -> str:
    value = headers.get(name)
    if value is None:
        raise RequestTargetError(f"The {name} header is required.")
    return value


def _reject_browser_credentials(
    headers: NormalizedHeaders,
    query_string: str | bytes,
) -> None:
    if headers.get("cookie") is not None:
        raise RequestTargetError(
            "Cookies are not accepted on browser protected routes."
        )
    if isinstance(query_string, bytes):
        try:
            raw_query = query_string.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RequestTargetError("The request query is invalid.") from exc
    elif isinstance(query_string, str):
        raw_query = query_string
    else:
        raise RequestTargetError("The request query is invalid.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw_query):
        raise RequestTargetError("The request query is invalid.")
    try:
        query_names = {
            name.casefold()
            for name, _ in parse_qsl(
                raw_query,
                keep_blank_values=True,
                encoding="utf-8",
                errors="strict",
            )
        }
    except (UnicodeDecodeError, ValueError) as exc:
        raise RequestTargetError("The request query is invalid.") from exc
    if query_names.intersection(_QUERY_CREDENTIAL_NAMES):
        raise RequestTargetError(
            "Query credentials are not accepted on browser protected routes."
        )


def _normalize_server(server: ServerAddress, *, scheme: str) -> str:
    if not isinstance(server, tuple) or len(server) != 2:
        raise RequestTargetError("The ASGI server address is invalid.")
    host, port = server
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        raise RequestTargetError("The ASGI server address is invalid.")
    if not 1 <= port <= 65535:
        raise RequestTargetError("The ASGI server port is invalid.")
    return _normalize_authority(_format_host_and_port(host, port), scheme=scheme)


def _normalize_authority(value: str, *, scheme: str) -> str:
    if not isinstance(value, str) or not value:
        raise RequestTargetError("The authority is invalid.")
    if any(char.isspace() for char in value) or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in value
    ):
        raise RequestTargetError("The authority is invalid.")
    if any(marker in value for marker in ("@", "/", "?", "#", "\\", "%", ",")):
        raise RequestTargetError("The authority is invalid.")

    host: str
    port: int | None
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            raise RequestTargetError("The IPv6 authority is invalid.")
        host = value[1:closing]
        suffix = value[closing + 1 :]
        if suffix and not suffix.startswith(":"):
            raise RequestTargetError("The authority port is invalid.")
        port = _parse_port(suffix[1:] if suffix else None)
        try:
            host = ipaddress.IPv6Address(host).compressed
        except ValueError as exc:
            raise RequestTargetError("The IPv6 authority is invalid.") from exc
        normalized_host = f"[{host.casefold()}]"
    else:
        if value.count(":") > 1:
            raise RequestTargetError("IPv6 authorities must use brackets.")
        if ":" in value:
            host, raw_port = value.rsplit(":", 1)
            port = _parse_port(raw_port)
        else:
            host = value
            port = None
        normalized_host = _normalize_host(host)

    if port is not None and not 1 <= port <= 65535:
        raise RequestTargetError("The authority port is invalid.")
    default_port = 80 if scheme == "http" else 443
    return (
        normalized_host if port in {None, default_port} else f"{normalized_host}:{port}"
    )


def _normalize_host(host: str) -> str:
    if not host:
        raise RequestTargetError("The authority host is empty.")
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise RequestTargetError("The authority host is invalid.") from exc
        if not ascii_host or any(
            not label or label.startswith("-") or label.endswith("-")
            for label in ascii_host.split(".")
        ):
            raise RequestTargetError("The authority host is invalid.")
        return ascii_host.casefold()
    return parsed_ip.compressed.casefold()


def _parse_port(value: str | None) -> int | None:
    if value is None:
        return None
    if not _PORT.fullmatch(value):
        raise RequestTargetError("The authority port is invalid.")
    port = int(value)
    if not 1 <= port <= 65535:
        raise RequestTargetError("The authority port is invalid.")
    return port


def _format_host_and_port(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _normalize_scheme(value: str) -> str:
    if not isinstance(value, str) or not _SCHEME.fullmatch(value):
        raise RequestTargetError("The request scheme is invalid.")
    scheme = value.casefold()
    if scheme not in {"http", "https"}:
        raise RequestTargetError("The request scheme must be HTTP or HTTPS.")
    return scheme


def _normalize_method(value: str) -> str:
    if not isinstance(value, str):
        raise RequestTargetError("The request method is invalid.")
    method = value.upper()
    if value != method or not _METHOD.fullmatch(method):
        raise RequestTargetError("The request method is invalid.")
    return method


def _normalize_path(path: str, *, raw_path: bytes | None) -> str:
    if raw_path is not None:
        if not isinstance(raw_path, bytes):
            raise RequestTargetError("The raw request path is invalid.")
        try:
            normalized = raw_path.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RequestTargetError("The raw request path is invalid.") from exc
    else:
        if not isinstance(path, str):
            raise RequestTargetError("The request path is invalid.")
        normalized = quote(path, safe=_PATH_SAFE)
    if not normalized.startswith("/") or any(
        marker in normalized for marker in ("?", "#", "\\")
    ):
        raise RequestTargetError("The request path is invalid.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in normalized):
        raise RequestTargetError("The request path is invalid.")
    for index, char in enumerate(normalized):
        if char == "%" and (
            index + 2 >= len(normalized)
            or normalized[index + 1] not in "0123456789abcdefABCDEF"
            or normalized[index + 2] not in "0123456789abcdefABCDEF"
        ):
            raise RequestTargetError(
                "The request path contains an invalid percent escape."
            )
    return normalized or "/"


def _decode_header_part(
    value: HeaderValue,
    *,
    field: str,
    strip_ows: bool = True,
) -> str:
    if isinstance(value, bytes):
        try:
            decoded = value.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise RequestTargetError(f"The {field} header is invalid.") from exc
    elif isinstance(value, str):
        decoded = value
    else:
        raise RequestTargetError(f"The {field} header is invalid.")
    if any(
        (ord(char) < 0x20 and char != "\t") or ord(char) == 0x7F for char in decoded
    ):
        raise RequestTargetError(f"The {field} header is invalid.")
    return decoded.strip(" \t") if strip_ows else decoded
