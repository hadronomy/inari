from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from inari.core.exceptions import AgentError
from inari.db.migrations import DatabaseMigrator
from inari.documents import (
    AdmissionAccepted,
    AdmissionRequest,
    DocumentAdmission,
    ManagedSubmissionContext,
)
from inari.gateway.bridges.runtime import GatewayCommandDispatcher
from inari.gateway.managed_dispatch import (
    ManagedDispatchVerifier,
    VerifiedManagedDispatch,
)
from inari.gateway.models import (
    AgentManagedScope,
    ControllerAction,
    Ed25519VerificationJwk,
    GatewayEnrollmentRecord,
    GatewayInboundCommandState,
    ManagedDispatchEnrollment,
    UpstreamDataPlaneKind,
    ZenohDataPlaneAuthKind,
    ZenohDataPlaneConfig,
    ZenohSerialization,
    ZenohSessionMode,
)
from inari.gateway.protocol import (
    ControllerDispatchDeviceWorkMessage,
    ManagedDeviceWorkPayload,
)
from inari.gateway.repositories import GatewayRepository
from inari.runtime.jobs.service import JobService
from inari.runtime.store import RuntimeStore

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeVerifier:
    verified: VerifiedManagedDispatch
    calls: int = 0

    def verify(self, message, *, enrollment, now=None) -> VerifiedManagedDispatch:
        del message, enrollment, now
        self.calls += 1
        return self.verified


@dataclass(slots=True)
class FakeAdmission:
    requests: list[AdmissionRequest] = field(default_factory=list)

    async def admit(self, request: AdmissionRequest) -> AdmissionAccepted:
        self.requests.append(request)
        return AdmissionAccepted(
            print_intent_id=request.work.context.print_intent_id,
            print_job_id="print-job-1",
            device_id=request.work.context.device_id,
            accepted_at=NOW + timedelta(seconds=1),
            state_version=1,
            replayed=False,
        )


@pytest.mark.anyio
async def test_dispatcher_admits_and_replays_managed_work_once(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    verifier = FakeVerifier(_verified())
    admission = FakeAdmission()
    dispatcher = GatewayCommandDispatcher(
        job_service=cast(JobService, object()),
        gateway_repository=repository,
        document_admission=cast(DocumentAdmission, admission),
        managed_dispatch_verifier=cast(ManagedDispatchVerifier, verifier),
    )
    message = _message(sequence=1)

    await dispatcher.handle_dispatch_device_work(message, enrollment=_enrollment())

    inbound = repository.get_inbound_command(message.command_id)
    assert inbound is not None
    assert inbound.state is GatewayInboundCommandState.ACCEPTED
    assert inbound.dispatch_epoch == 7
    assert inbound.job_id == "print-job-1"
    assert len(admission.requests) == 1
    request = admission.requests[0]
    assert isinstance(request.work.context, ManagedSubmissionContext)
    assert request.work.context.managed_work_id == "mw_test"
    assert request.work.document.content == b"^XA^FDInari^FS^XZ"
    trust = _enrollment().managed_dispatch
    assert trust is not None
    assert repository.list_pending_outbox() == ()
    assert (
        repository.list_pending_outbox(
            recipient_scope=AgentManagedScope(
                organization_id="org_other", site_id="site_other", agent_id="agt_other"
            )
        )
        == ()
    )
    pending = repository.list_pending_outbox(recipient_scope=trust.scope)
    assert len(pending) == 1
    repository.mark_outbox_sent(pending[0].message_id)

    await dispatcher.handle_dispatch_device_work(message, enrollment=_enrollment())

    assert len(admission.requests) == 1
    assert len(repository.list_pending_outbox(recipient_scope=trust.scope)) == 1


@pytest.mark.anyio
async def test_dispatcher_blocks_a_sequence_gap_before_document_admission(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    verifier = FakeVerifier(_verified(sequence=2))
    admission = FakeAdmission()
    dispatcher = GatewayCommandDispatcher(
        job_service=cast(JobService, object()),
        gateway_repository=repository,
        document_admission=cast(DocumentAdmission, admission),
        managed_dispatch_verifier=cast(ManagedDispatchVerifier, verifier),
    )

    with pytest.raises(AgentError, match="Expected Controller command sequence 1"):
        await dispatcher.handle_dispatch_device_work(
            _message(sequence=2),
            enrollment=_enrollment(),
        )

    assert admission.requests == []


def _repository(tmp_path: Path) -> GatewayRepository:
    database_path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(database_path).ensure_current()
    return GatewayRepository(RuntimeStore(database_path))


def _message(*, sequence: int) -> ControllerDispatchDeviceWorkMessage:
    return ControllerDispatchDeviceWorkMessage.model_validate(
        {
            "type": "controller.command.dispatch_device_work",
            "message_id": "msg_test",
            "command_id": "command_test",
            "sequence": sequence,
            "issued_at": NOW.isoformat(),
            "payload": {
                "managed_work_id": "mw_test",
                "authenticated_data": {
                    "organization_id": "org_test",
                    "site_id": "site_test",
                    "agent_id": "agt_test",
                    "managed_work_id": "mw_test",
                    "idempotency_key": "report:manual:17:1",
                    "payload_fingerprint": "a" * 64,
                    "dispatch_epoch": 7,
                    "sequence": sequence,
                    "issued_at": int(NOW.timestamp()),
                    "expires_at": int((NOW + timedelta(minutes=2)).timestamp()),
                    "work_expires_at": (NOW + timedelta(minutes=2)).isoformat(),
                },
                "sealed_envelope": {
                    "protocol_version": 1,
                    "key_id": "dispatch-test",
                    "suite": ("dhkem_x25519_hkdf_sha256_hkdf_sha256_aes256_gcm"),
                    "encapsulated_key_base64url": "a",
                    "ciphertext_base64url": "a",
                },
            },
        }
    )


def _verified(*, sequence: int = 1) -> VerifiedManagedDispatch:
    return VerifiedManagedDispatch(
        managed_work_id="mw_test",
        idempotency_key="report:manual:17:1",
        work=ManagedDeviceWorkPayload.model_validate(
            {
                "contract_major": 1,
                "scope": {
                    "database": "odoo",
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
                    "content_base64": "not-used-after-verification",
                },
                "normalized_device_options": {"copies": 1},
            }
        ),
        document_bytes=b"^XA^FDInari^FS^XZ",
        dispatch_epoch=7,
        sequence=sequence,
        expires_at=NOW + timedelta(minutes=2),
    )


def _enrollment() -> GatewayEnrollmentRecord:
    return GatewayEnrollmentRecord(
        enrolled_at=NOW,
        data_plane=ZenohDataPlaneConfig(
            kind=UpstreamDataPlaneKind.ZENOH,
            session_mode=ZenohSessionMode.CLIENT,
            connect_endpoints=("tls/router.example:7447",),
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
                x="a" * 43,
                kid="controller-key",
            ),
        ),
    )
