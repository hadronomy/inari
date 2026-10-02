import base64
import json
from unittest.mock import Mock

import pytest
import requests

from inari_odoo_services.http_client import JsonHttpClient, RemoteServiceError
from inari_odoo_services.managed_work import ManagedWorkClient
from inari_odoo_services.openbao import KubernetesTokenProvider, OpenBaoClient
from inari_odoo_services.workload_identity import (
    WorkloadScope,
    WorkloadTokenProvider,
    credential_path,
)


SCOPE = WorkloadScope("production", "7", "org_example")
IDENTITY = {
    "database": "production",
    "company_id": "7",
    "organization_id": "org_example",
}


def response(document=None, *, status=200, chunks=None):
    value = Mock()
    value.status_code = status
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    value.iter_content.return_value = (
        chunks if chunks is not None else [json.dumps(document).encode()]
    )
    return value


def transport(*responses, limit=65536):
    session = Mock()
    session.request.side_effect = list(responses)
    return JsonHttpClient(
        "https://controller.example", session=session, response_limit=limit
    ), session


@pytest.mark.parametrize(
    "address",
    [
        "http://host",
        "https://user:secret@host",
        "https://host/?secret=x",
        "https://host/#x",
        "https://host/path",
        "https://host\n",
    ],
)
def test_transport_rejects_ambiguous_origins(address):
    with pytest.raises(RemoteServiceError):
        JsonHttpClient(address)


def test_transport_disables_redirects_and_bounds_chunked_responses():
    http, session = transport(response(chunks=[b"x" * 4, b"x" * 5]), limit=8)
    with pytest.raises(RemoteServiceError, match="exceeds"):
        http.request("GET", "/record", headers={"Authorization": "Bearer token"})
    kwargs = session.request.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["verify"] is True
    assert session.trust_env is False


def test_transport_does_not_disclose_rejected_response_body():
    rejected = response({"secret": "must-not-escape"}, status=302)
    http, _ = transport(rejected)
    with pytest.raises(RemoteServiceError) as error:
        http.request("GET", "/record")
    assert error.value.status == 302
    assert "must-not-escape" not in str(error.value)
    rejected.iter_content.assert_not_called()


def test_openbao_reloads_rotated_workload_token(tmp_path):
    token = tmp_path / "token"
    token.write_text("first-workload-token")
    http, session = transport(
        response({"auth": {"client_token": "first-bao-token", "lease_duration": 10}}),
        response({"auth": {"client_token": "second-bao-token", "lease_duration": 10}}),
    )
    clock = Mock(return_value=0)
    provider = KubernetesTokenProvider(
        client=OpenBaoClient(http),
        role="odoo",
        auth_mount="kubernetes",
        service_account_token_file=token,
        clock=clock,
    )
    assert provider.token() == "first-bao-token"
    assert provider.token() == "first-bao-token"
    token.write_text("rotated-workload-token")
    clock.return_value = 6
    assert provider.token() == "second-bao-token"
    assert session.request.call_count == 2
    assert session.request.call_args.kwargs["json"]["jwt"] == "rotated-workload-token"


def token_provider(*, discovery=None, secret=None, token=None):
    discovery = discovery or {
        "issuer": "https://controller.example",
        "token_endpoint": "https://controller.example/oauth/token",
    }
    http, session = transport(
        response(discovery),
        response(
            token
            or {"access_token": "oidc-token", "token_type": "Bearer", "expires_in": 300}
        ),
    )
    bao = Mock()
    bao.request.return_value = {
        "data": {
            "data": secret
            if secret is not None
            else {
                **IDENTITY,
                "issuer_url": "https://controller.example",
                "client_id": "client:id",
                "client_secret": "secret:+&",
                "scopes": ["managed_work:read", "managed_work:write"],
            }
        }
    }
    bao_tokens = Mock()
    bao_tokens.token.return_value = "bao-token"
    clock = Mock(return_value=0)
    provider = WorkloadTokenProvider(
        scope=SCOPE,
        issuer="https://controller.example",
        client_id="client:id",
        openbao=bao,
        openbao_tokens=bao_tokens,
        http=http,
        clock=clock,
    )
    return provider, session, bao, clock


def test_oidc_form_encodes_credentials_and_caches_only_short_lived_tokens():
    provider, session, bao, _ = token_provider()
    assert provider.token() == "oidc-token"
    assert provider.token() == "oidc-token"
    assert session.request.call_count == 2
    bao.request.assert_called_once_with(
        "GET", "/v1/secret/data/" + credential_path(SCOPE), token="bao-token"
    )
    call = session.request.call_args.kwargs
    request = requests.Request(
        "POST",
        "https://controller.example/oauth/token",
        auth=call["auth"],
        data=call["data"],
    ).prepare()
    assert (
        base64.b64decode(request.headers["Authorization"].split()[1])
        == b"client%3Aid:secret%3A%2B%26"
    )
    assert (
        request.body
        == "grant_type=client_credentials&scope=managed_work%3Aread+managed_work%3Awrite"
    )


def test_oidc_rereads_credentials_after_token_expiry():
    provider, session, bao, clock = token_provider()
    assert provider.token() == "oidc-token"
    session.request.side_effect = [
        response(
            {
                "issuer": "https://controller.example",
                "token_endpoint": "https://controller.example/oauth/token",
            }
        ),
        response(
            {"access_token": "rotated-token", "token_type": "Bearer", "expires_in": 300}
        ),
    ]
    bao.request.return_value["data"]["data"]["client_secret"] = "rotated-secret"
    clock.return_value = 280
    assert provider.token() == "rotated-token"
    assert bao.request.call_count == 2
    assert session.request.call_args.kwargs["auth"].password == "rotated-secret"


def test_oidc_never_sends_credentials_to_discovered_foreign_origin():
    provider, session, bao, _ = token_provider(
        discovery={
            "issuer": "https://controller.example",
            "token_endpoint": "https://attacker.example/token",
        }
    )
    with pytest.raises(RemoteServiceError, match="outside"):
        provider.token()
    assert session.request.call_count == 1
    bao.request.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("database", "other"),
        ("company_id", "8"),
        ("organization_id", "org_other"),
        ("client_id", "other"),
        ("issuer_url", "https://other.example"),
    ],
)
def test_oidc_rejects_credentials_from_another_identity(field, value):
    provider, session, bao, _ = token_provider()
    bao.request.return_value["data"]["data"][field] = value
    with pytest.raises(RemoteServiceError, match="company identity"):
        provider.token()
    assert session.request.call_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_in", 3600),
        ("expires_in", True),
        ("expires_in", 0),
        ("token_type", "DPoP"),
        ("token_type", None),
        ("access_token", "with spaces"),
    ],
)
def test_oidc_rejects_invalid_token_contract(field, value):
    token = {
        "access_token": "token",
        "token_type": "Bearer",
        "expires_in": 300,
        field: value,
    }
    provider, *_ = token_provider(token=token)
    with pytest.raises(RemoteServiceError, match="short-lived"):
        provider.token()


def work_record(**changes):
    return {
        "managed_work_id": "mw_test",
        "print_intent_id": "pi_v1_test",
        "device_id": "dev_test",
        "state": "dispatching",
        "scope": {**IDENTITY, "site_id": "site_test", "agent_id": "agt_test"},
        "expires_at": "2026-09-07T12:05:00Z",
        **changes,
    }


def managed_client(*responses):
    http, session = transport(*responses)
    tokens = Mock()
    tokens.scope = SCOPE
    tokens.token.return_value = "access-token"
    return ManagedWorkClient(http, tokens), session, tokens


def test_managed_client_rejects_cross_company_submission_before_network_io():
    client, session, _ = managed_client()
    with pytest.raises(RemoteServiceError, match="company identity"):
        client.submit(
            "pi_v1_test", {"work": {"scope": {**IDENTITY, "company_id": "8"}}}
        )
    session.request.assert_not_called()


def test_managed_find_returns_none_only_for_not_found():
    client, session, _ = managed_client(response(status=404), response(status=503))
    assert client.find("pi_v1_test") is None
    with pytest.raises(RemoteServiceError) as error:
        client.find("pi_v1_test")
    assert error.value.status == 503
    assert session.request.call_args.args[1].endswith(
        "/managed-work/by-idempotency-key"
    )
    assert (
        session.request.call_args.kwargs["headers"]["Idempotency-Key"] == "pi_v1_test"
    )


def test_managed_get_rejects_cross_company_response():
    client, *_ = managed_client(
        response(work_record(scope={**IDENTITY, "company_id": "8"}))
    )
    with pytest.raises(RemoteServiceError, match="company scope"):
        client.get("mw_test")


def test_managed_client_refreshes_once_after_unauthorized():
    client, session, tokens = managed_client(
        response(status=401), response(work_record())
    )
    tokens.token.side_effect = ["old-token", "new-token"]
    assert client.get("mw_test")["state"] == "dispatching"
    tokens.invalidate.assert_called_once()
    assert (
        session.request.call_args.kwargs["headers"]["Authorization"]
        == "Bearer new-token"
    )


def test_managed_client_never_retries_an_uncertain_submission():
    client, session, _ = managed_client()
    session.request.side_effect = requests.Timeout("may have been admitted")
    with pytest.raises(RemoteServiceError):
        client.submit("pi_v1_test", {"work": {"scope": IDENTITY}})
    assert session.request.call_count == 1


@pytest.mark.parametrize("ca", [False, "", "   ", None])
def test_transport_cannot_disable_tls_through_empty_ca_configuration(ca):
    with pytest.raises(RemoteServiceError, match="configuration"):
        JsonHttpClient("https://controller.example", ca_certificate=ca)


def test_workload_authentication_recovers_a_revoked_openbao_token():
    provider, _, bao, _ = token_provider()
    secret = bao.request.return_value
    bao.request.side_effect = [RemoteServiceError("denied", status=403), secret]
    assert provider.token() == "oidc-token"
    provider._openbao_tokens.invalidate.assert_called_once()
    assert bao.request.call_count == 2


def test_pairing_signer_keeps_its_transit_key_version_after_transport_extraction():
    from inari_odoo_services.pairing_assertions import PairingAssertionSigner

    client = Mock()
    client.request.side_effect = [
        {
            "data": {
                "latest_version": 2,
                "type": "ed25519",
                "supports_signing": True,
                "keys": {"2": {"public_key": "public-key"}},
            }
        },
        {"data": {"signature": "vault:v2:" + base64.b64encode(bytes(64)).decode()}},
    ]
    tokens = Mock()
    tokens.token.return_value = "bao-token"
    signer = PairingAssertionSigner(
        client=client,
        token_provider=tokens,
        transit_mount="transit",
        key_name="pairing-test",
    )
    signed = signer.sign({"sub": "pairing-request"})
    assert signed.signer_key_id == "pairing-test:v2"
    assert client.request.call_args.kwargs["json_body"]["key_version"] == 2
    assert (
        base64.b64decode(client.request.call_args.kwargs["json_body"]["input"]).decode()
        == signed.compact_jws.rsplit(".", 1)[0]
    )
