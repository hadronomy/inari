from __future__ import annotations

import base64
from datetime import UTC, datetime
from hashlib import sha256

import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pyhpke import AEADId, KDFId, KEMId, CipherSuite

from inari.core.exceptions import AgentError
from inari.gateway.managed_dispatch import ManagedDispatchVerifier
from inari.gateway.models import (
    AgentManagedScope,
    ControllerAction,
    Ed25519VerificationJwk,
    GatewayEnrollmentRecord,
    ManagedDispatchEnrollment,
    UpstreamDataPlaneKind,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
)
from inari.gateway.protocol import ControllerDispatchDeviceWorkMessage
from inari.security.dispatch_keys import DispatchEncryptionKeyService
from inari.security.secrets import MemorySecretStore


def test_verifier_opens_a_canonical_signed_hpke_dispatch() -> None:
    fixture = _dispatch_fixture()

    verified = fixture.verifier.verify(
        fixture.message,
        enrollment=fixture.enrollment,
        now=datetime(2026, 9, 4, 12, 0, 30, tzinfo=UTC),
    )

    assert verified.managed_work_id == "mw_test"
    assert verified.document_bytes == b"^XA^FO20,20^FDInari^FS^XZ"
    assert verified.work.device_id == "dev_label"
    assert verified.dispatch_epoch == 7
    assert verified.sequence == 42


def test_verifier_rejects_outer_authenticated_data_changes() -> None:
    fixture = _dispatch_fixture()
    payload = fixture.message.payload.model_copy(
        update={
            "authenticated_data": fixture.message.payload.authenticated_data.model_copy(
                update={"payload_fingerprint": "0" * 64}
            )
        }
    )
    message = fixture.message.model_copy(update={"payload": payload})

    with pytest.raises(AgentError, match="could not be authenticated") as captured:
        fixture.verifier.verify(
            message,
            enrollment=fixture.enrollment,
            now=datetime(2026, 9, 4, 12, 0, 30, tzinfo=UTC),
        )

    assert captured.value.code == "MANAGED_DISPATCH_DECRYPTION_FAILED"


def test_verifier_rejects_expired_dispatch_before_decryption() -> None:
    fixture = _dispatch_fixture()

    with pytest.raises(AgentError, match="has expired") as captured:
        fixture.verifier.verify(
            fixture.message,
            enrollment=fixture.enrollment,
            now=datetime(2026, 9, 4, 12, 3, tzinfo=UTC),
        )

    assert captured.value.code == "MANAGED_DISPATCH_EXPIRED"


class _DispatchFixture:
    def __init__(
        self,
        *,
        verifier: ManagedDispatchVerifier,
        message: ControllerDispatchDeviceWorkMessage,
        enrollment: GatewayEnrollmentRecord,
    ) -> None:
        self.verifier = verifier
        self.message = message
        self.enrollment = enrollment


def _dispatch_fixture() -> _DispatchFixture:
    dispatch_keys = DispatchEncryptionKeyService(MemorySecretStore())
    recipient = dispatch_keys.get_or_create()
    signing_key = Ed25519PrivateKey.generate()
    public_signing_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    document = b"^XA^FO20,20^FDInari^FS^XZ"
    aad = {
        "organization_id": "org_test",
        "site_id": "site_test",
        "agent_id": "agt_test",
        "managed_work_id": "mw_test",
        "print_intent_id": "pi_test",
        "payload_fingerprint": sha256(document).hexdigest(),
        "dispatch_epoch": 7,
        "sequence": 42,
        "issued_at": 1_788_523_200,
        "expires_at": 1_788_523_320,
    }
    claims = {
        "iss": "controller-primary",
        "aud": "agt_test",
        "authenticated_data": aad,
        "work": {
            "contract_major": 1,
            "scope": {
                "database": "production",
                "company_id": "7",
                "organization_id": "org_test",
                "site_id": "site_test",
                "agent_id": "agt_test",
            },
            "print_intent_id": "pi_test",
            "device_id": "dev_label",
            "origin": {
                "binding": {
                    "report_binding_id": "binding-1",
                    "binding_revision_id": "revision-1",
                    "report_action_id": "stock.action_report_delivery",
                    "report_contract_digest": "contract-digest",
                    "template_digest": "template-digest",
                    "command_profile_id": None,
                    "layout_profile_id": None,
                    "hardware_matrix_digest": None,
                },
                "route": "manual",
                "source": {
                    "kind": "records",
                    "model": "stock.picking",
                    "ordered_ids": [17],
                },
                "rendered_document_index": 0,
                "copy_ordinal": 1,
            },
            "document": {
                "operation": "label_document",
                "content_base64": base64.b64encode(document).decode("ascii"),
            },
            "normalized_device_options": {"copies": 1},
        },
    }
    protected = _base64url(
        rfc8785.dumps(
            {
                "alg": "EdDSA",
                "kid": "controller-dispatch-key",
                "typ": "application/inari-dispatch+jws",
            }
        )
    )
    payload = _base64url(rfc8785.dumps(claims))
    signing_input = f"{protected}.{payload}".encode("ascii")
    compact_jws = (
        f"{protected}.{payload}.{_base64url(signing_key.sign(signing_input))}"
    ).encode("ascii")

    suite = CipherSuite.new(
        KEMId.DHKEM_X25519_HKDF_SHA256,
        KDFId.HKDF_SHA256,
        AEADId.AES256_GCM,
    )
    recipient_public_key = suite.kem.deserialize_public_key(
        recipient.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    info = (
        f"inari-managed-dispatch\0v1\0agt_test\0{recipient.key_id}".encode()
    )
    encapsulated_key, sender = suite.create_sender_context(
        recipient_public_key,
        info=info,
    )
    ciphertext = sender.seal(compact_jws, aad=rfc8785.dumps(aad))
    message = ControllerDispatchDeviceWorkMessage.model_validate(
        {
            "type": "controller.command.dispatch_device_work",
            "message_id": "msg_test",
            "command_id": "job_dispatch_test",
            "sequence": 42,
            "issued_at": "2026-09-04T12:00:00Z",
            "payload": {
                "managed_work_id": "mw_test",
                "authenticated_data": aad,
                "sealed_envelope": {
                    "protocol_version": 1,
                    "key_id": recipient.key_id,
                    "suite": (
                        "dhkem_x25519_hkdf_sha256_hkdf_sha256_aes256_gcm"
                    ),
                    "encapsulated_key_base64url": _base64url(encapsulated_key),
                    "ciphertext_base64url": _base64url(ciphertext),
                },
            },
        }
    )
    enrollment = GatewayEnrollmentRecord(
        enrolled_at=datetime(2026, 9, 4, tzinfo=UTC),
        data_plane=ZenohDataPlaneConfig(
            kind=UpstreamDataPlaneKind.ZENOH,
            session_mode=ZenohSessionMode.CLIENT,
            connect_endpoints=("tcp/example.test:7447",),
            namespace="iot/v1/agents/agt_test",
            serialization=ZenohSerialization.JSON,
            auth_kind=ZenohDataPlaneAuthKind.MTLS,
        ),
        controller_actions=(ControllerAction.MANAGED_WORK_DISPATCH,),
        controller_instance_id="controller-primary",
        managed_dispatch=ManagedDispatchEnrollment(
            scope=AgentManagedScope(
                organization_id="org_test",
                site_id="site_test",
                agent_id="agt_test",
            ),
            issuer="controller-primary",
            epoch=7,
            verification_jwk=Ed25519VerificationJwk(
                x=_base64url(public_signing_key),
                kid="controller-dispatch-key",
            ),
        ),
    )
    return _DispatchFixture(
        verifier=ManagedDispatchVerifier(dispatch_keys),
        message=message,
        enrollment=enrollment,
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
