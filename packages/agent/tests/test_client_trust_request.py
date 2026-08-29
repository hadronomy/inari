from __future__ import annotations

import pytest

from inari.client_trust.request import (
    RequestTargetError,
    build_browser_request,
    build_request_target,
    normalize_headers,
    normalize_origin,
)


def test_request_target_uses_trusted_server_and_strips_query_from_htu() -> None:
    target = build_request_target(
        scheme="HTTPS",
        server=("Agent.Example", 443),
        path="/local/v1/device-work",
        headers=[(b"Host", b"agent.example:443")],
    )

    assert target.htu == "https://agent.example/local/v1/device-work"


def test_request_target_drops_http_default_port() -> None:
    target = build_request_target(
        scheme="http",
        server=("agent.example", 80),
        path="/health",
        headers=[(b"host", b"agent.example:80")],
    )

    assert target.authority == "agent.example"
    assert target.htu == "http://agent.example/health"


def test_request_target_uses_raw_path_without_query_or_fragment() -> None:
    target = build_request_target(
        scheme="https",
        server=("agent.example", 8443),
        path="/ignored",
        raw_path=b"/device%20work",
    )

    assert target.htu == "https://agent.example:8443/device%20work"


def test_ipv6_authority_and_default_port_are_normalized() -> None:
    target = build_request_target(
        scheme="https",
        server=("2001:0DB8:0:0:0:0:0:1", 443),
        path="/health",
        headers=[(b"host", b"[2001:db8::1]:443")],
    )

    assert target.authority == "[2001:db8::1]"
    assert target.htu == "https://[2001:db8::1]/health"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://EXAMPLE.COM:443", "https://example.com"),
        ("http://[2001:0DB8::1]:80", "http://[2001:db8::1]"),
        ("http://example.com:8069", "http://example.com:8069"),
        ("https://[2001:db8::1]:0443", "https://[2001:db8::1]"),
        ("http://EXAMPLE.COM:0080", "http://example.com"),
    ],
)
def test_normalize_origin(value: str, expected: str) -> None:
    assert normalize_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "null",
        "*",
        "file:///tmp/report",
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "https://example.com#fragment",
        "https://example.com:bad",
        "https://example.com:0",
        "https://example.com:65536",
        "https://[2001:db8::1",
    ],
)
def test_normalize_origin_rejects_non_exact_origins(value: str | None) -> None:
    with pytest.raises(RequestTargetError):
        normalize_origin(value)


@pytest.mark.parametrize(
    "headers",
    [
        [("Host", "agent.example"), ("host", "agent.example")],
        [("Authorization", "DPoP one"), ("authorization", "DPoP two")],
        [("DPoP", "one"), ("dpop", "two")],
        [("Origin", "https://one.example"), ("origin", "https://two.example")],
        [("Authorization", "DPoP one,two")],
        [("DPoP", "one,two")],
        [("Origin", "https://one.example,https://two.example")],
        [("Cookie", "one=1"), ("cookie", "two=2")],
        [(" Authorization", "DPoP one")],
        [("Authorization", "DPoP\r\n one")],
    ],
)
def test_security_headers_have_exact_cardinality(headers: list[tuple[str, str]]) -> None:
    with pytest.raises(RequestTargetError):
        normalize_headers(headers)


def test_forwarded_host_is_rejected() -> None:
    with pytest.raises(RequestTargetError):
        normalize_headers([("X-Forwarded-Host", "evil.example")])


def test_host_must_match_trusted_server_authority() -> None:
    with pytest.raises(RequestTargetError):
        build_request_target(
            scheme="https",
            server=("agent.example", 443),
            path="/health",
            headers=[("Host", "evil.example")],
        )


def test_browser_request_rejects_query_credentials_and_cookies() -> None:
    headers = [
        ("Origin", "https://odoo.example"),
        ("Authorization", "DPoP access-token"),
        ("DPoP", "proof"),
    ]
    with pytest.raises(RequestTargetError):
        build_browser_request(
            scheme="https",
            server=("agent.example", 443),
            path="/local/v1/device-work",
            headers=headers,
            query_string="access_token=secret",
        )
    with pytest.raises(RequestTargetError):
        build_browser_request(
            scheme="https",
            server=("agent.example", 443),
            path="/local/v1/device-work",
            headers=[*headers, ("Cookie", "session=secret")],
        )
    with pytest.raises(RequestTargetError):
        build_browser_request(
            scheme="https",
            server=("agent.example", 443),
            path="/local/v1/device-work",
            headers=headers,
            query_string=b"%ff=1",
        )


def test_browser_request_accepts_non_sensitive_query() -> None:
    request = build_browser_request(
        scheme="https",
        server=("agent.example", 443),
        path="/local/v1/device-work",
        headers=[
            ("Origin", "https://odoo.example:443"),
            ("Authorization", "DPoP access-token"),
            ("DPoP", "proof"),
        ],
        query_string="request_id=one",
    )

    assert request.target.htu == "https://agent.example/local/v1/device-work"
    assert request.origin == "https://odoo.example"
    assert request.authorization == "DPoP access-token"
    assert request.dpop == "proof"


def test_browser_request_requires_exact_origin_authorization_and_dpop() -> None:
    with pytest.raises(RequestTargetError):
        build_browser_request(
            scheme="https",
            server=("agent.example", 443),
            path="/local/v1/device-work",
            headers=[("Authorization", "DPoP access-token"), ("DPoP", "proof")],
        )
