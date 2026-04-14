from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import replace
from datetime import timedelta

from ..config import AgentSettings
from ..gateway.enrollment import GatewayEnrollmentService
from ..gateway.models import (
    GatewayEnrollmentRecord,
    ManagedCertificateFailureReason,
    ManagedCertificateOperation,
    ManagedCertificateState,
    ManagedCertificateStatus,
    UpstreamCertificateMode,
)
from ..runtime.models import utc_now
from .certificate_provisioners import ClientCertificateProvisioner, ManagedCertificateProvisioningError
from .certificates import CertificateLifecycleService, ManagedCertificate, ManagedCertificateInspection

logger = logging.getLogger(__name__)


class ManagedCertificateLifecycleManager:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        enrollment_service: GatewayEnrollmentService,
        certificate_service: CertificateLifecycleService,
        certificate_provisioner: ClientCertificateProvisioner,
    ) -> None:
        self.settings = settings
        self.enrollment_service = enrollment_service
        self.certificate_service = certificate_service
        self.certificate_provisioner = certificate_provisioner
        self._lock = asyncio.Lock()
        self._backoff = _LifecycleBackoff(
            base_delay=settings.gateway_backoff_base_seconds,
            max_delay=settings.gateway_backoff_max_seconds,
        )
        self._status = ManagedCertificateStatus(
            state=(
                ManagedCertificateState.WAITING_FOR_ENROLLMENT
                if settings.upstream_certificate_mode is UpstreamCertificateMode.STEP_CA
                else ManagedCertificateState.DISABLED
            ),
            detail=(
                "Awaiting managed enrollment before certificate lifecycle can begin."
                if settings.upstream_certificate_mode is UpstreamCertificateMode.STEP_CA
                else "Managed certificate lifecycle is disabled."
            ),
        )

    def current_status(self) -> ManagedCertificateStatus:
        return self._status

    async def ensure_current(
        self,
        *,
        enrollment: GatewayEnrollmentRecord | None = None,
        trigger: str = "manual",
    ) -> ManagedCertificate | None:
        if self.settings.upstream_certificate_mode is not UpstreamCertificateMode.STEP_CA:
            inspection = self.certificate_service.inspect_current_certificate()
            status = self._status_from_observation(
                inspection,
                state=ManagedCertificateState.DISABLED,
                detail="Managed certificate lifecycle is disabled.",
                last_checked_at=utc_now(),
            )
            self._status = status
            return inspection.certificate

        async with self._lock:
            return await self._ensure_current_locked(enrollment=enrollment, trigger=trigger)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.ensure_current(trigger="supervisor")
            except Exception:
                logger.exception("Managed certificate lifecycle loop failed")
            await asyncio.sleep(self._sleep_seconds())

    async def _ensure_current_locked(
        self,
        *,
        enrollment: GatewayEnrollmentRecord | None,
        trigger: str,
    ) -> ManagedCertificate | None:
        now = utc_now()
        record = enrollment or self.enrollment_service.load_enrollment()
        inspection = self.certificate_service.inspect_current_certificate()
        bootstrap_pending = self._bootstrap_pending(record)

        if record is None:
            self._status = self._status_from_observation(
                inspection,
                state=ManagedCertificateState.WAITING_FOR_ENROLLMENT,
                detail="Managed certificate lifecycle is waiting for upstream enrollment.",
                bootstrap_pending=False,
                last_checked_at=now,
            )
            return inspection.certificate

        if inspection.error_detail is not None:
            if bootstrap_pending:
                logger.warning(
                    "Managed client certificate is invalid during %s; clearing local certificate and re-bootstrapping.",
                    trigger,
                )
                self.certificate_service.clear_managed_certificate(keep_certificate_authority=True)
                inspection = self.certificate_service.inspect_current_certificate()
            else:
                self._status = self._status_from_observation(
                    inspection,
                    state=ManagedCertificateState.REBOOTSTRAP_REQUIRED,
                    operation=ManagedCertificateOperation.INSPECT,
                    failure_reason=ManagedCertificateFailureReason.LOCAL_CERTIFICATE_INVALID,
                    detail=f"The installed managed client certificate is invalid: {inspection.error_detail}",
                    bootstrap_pending=False,
                    last_checked_at=now,
                    last_failure_at=now,
                    next_action_at=now,
                )
                return None

        current = inspection.certificate
        if current is None and not bootstrap_pending:
            self._status = self._status_from_observation(
                inspection,
                state=ManagedCertificateState.WAITING_FOR_BOOTSTRAP,
                failure_reason=ManagedCertificateFailureReason.BOOTSTRAP_REQUIRED,
                detail="The agent has no managed client certificate and is waiting for fresh bootstrap material.",
                bootstrap_pending=False,
                last_checked_at=now,
                next_action_at=now,
            )
            return None

        renewal_due = self.certificate_service.certificate_needs_renewal(
            skew_seconds=self.settings.step_ca_certificate_renewal_skew_seconds
        )
        if current is not None and not renewal_due:
            self._backoff.reset()
            self._status = self._status_from_observation(
                inspection,
                state=ManagedCertificateState.VALID,
                detail="Managed client certificate is healthy.",
                bootstrap_pending=bootstrap_pending,
                last_checked_at=now,
                last_success_at=self._status.last_success_at or now,
                next_action_at=_renewal_deadline(current, self.settings.step_ca_certificate_renewal_skew_seconds),
            )
            return current

        operation = ManagedCertificateOperation.ISSUE if current is None else ManagedCertificateOperation.RENEW
        active_state = ManagedCertificateState.BOOTSTRAPPING if current is None else ManagedCertificateState.RENEWING
        detail = (
            "Bootstrapping a managed client certificate from step-ca."
            if current is None
            else "Renewing the managed client certificate with step-ca."
        )
        self._status = self._status_from_observation(
            inspection,
            state=active_state,
            operation=operation,
            detail=detail,
            bootstrap_pending=bootstrap_pending,
            last_checked_at=now,
            last_operation_at=now,
        )

        try:
            certificate = await self.certificate_provisioner.ensure_certificate(record)
        except ManagedCertificateProvisioningError as exc:
            self._status = self._status_for_failure(
                inspection=self.certificate_service.inspect_current_certificate(),
                now=now,
                bootstrap_pending=self._bootstrap_pending(record),
                error=exc,
            )
            return current if current is not None and not _is_expired(current) else None
        except Exception as exc:
            unknown = ManagedCertificateProvisioningError(
                "STEP_CA_LIFECYCLE_FAILED",
                f"Managed certificate lifecycle failed unexpectedly: {exc}",
                operation=operation,
                failure_reason=ManagedCertificateFailureReason.UNKNOWN,
                retryable=True,
            )
            self._status = self._status_for_failure(
                inspection=self.certificate_service.inspect_current_certificate(),
                now=now,
                bootstrap_pending=self._bootstrap_pending(record),
                error=unknown,
            )
            return current if current is not None and not _is_expired(current) else None

        if certificate is None:
            self._status = self._status_from_observation(
                self.certificate_service.inspect_current_certificate(),
                state=ManagedCertificateState.WAITING_FOR_BOOTSTRAP,
                operation=operation,
                failure_reason=ManagedCertificateFailureReason.BOOTSTRAP_REQUIRED,
                detail="step-ca could not issue a certificate because bootstrap material is missing.",
                bootstrap_pending=False,
                last_checked_at=now,
                last_operation_at=now,
                last_failure_at=now,
            )
            return current

        persisted_record = self.enrollment_service.load_enrollment() or record
        self.enrollment_service.persist_certificate_state(
            persisted_record,
            certificate=certificate,
            clear_bootstrap_ott=True,
        )
        self._backoff.reset()
        updated_inspection = self.certificate_service.inspect_current_certificate()
        self._status = self._status_from_observation(
            updated_inspection,
            state=ManagedCertificateState.VALID,
            detail=(
                "Managed client certificate bootstrapped successfully."
                if operation is ManagedCertificateOperation.ISSUE
                else "Managed client certificate renewed successfully."
            ),
            bootstrap_pending=False,
            operation=ManagedCertificateOperation.IDLE,
            failure_reason=ManagedCertificateFailureReason.NONE,
            last_checked_at=now,
            last_operation_at=now,
            last_success_at=now,
            next_action_at=_renewal_deadline(certificate, self.settings.step_ca_certificate_renewal_skew_seconds),
            successful_issue_count=self._status.successful_issue_count
            + (1 if operation is ManagedCertificateOperation.ISSUE else 0),
            successful_renewal_count=self._status.successful_renewal_count
            + (1 if operation is ManagedCertificateOperation.RENEW else 0),
        )
        return certificate

    def _status_for_failure(
        self,
        *,
        inspection: ManagedCertificateInspection,
        now,
        bootstrap_pending: bool,
        error: ManagedCertificateProvisioningError,
    ) -> ManagedCertificateStatus:
        retry_delay = self._backoff.next_delay() if error.retryable else None
        next_action_at = now + timedelta(seconds=retry_delay) if retry_delay is not None else now
        current = inspection.certificate
        expired = current is not None and _is_expired(current)
        state = ManagedCertificateState.RENEWAL_FAILED
        if error.rebootstrap_required:
            state = ManagedCertificateState.REBOOTSTRAP_REQUIRED
        elif expired:
            state = ManagedCertificateState.EXPIRED
        elif current is None and not bootstrap_pending:
            state = ManagedCertificateState.WAITING_FOR_BOOTSTRAP

        return self._status_from_observation(
            inspection,
            state=state,
            operation=error.operation,
            failure_reason=error.failure_reason,
            detail=error.message,
            bootstrap_pending=bootstrap_pending,
            retry_delay_seconds=retry_delay,
            last_checked_at=now,
            last_operation_at=now,
            last_failure_at=now,
            next_action_at=next_action_at,
            failed_issue_count=self._status.failed_issue_count
            + (1 if error.operation is ManagedCertificateOperation.ISSUE else 0),
            failed_renewal_count=self._status.failed_renewal_count
            + (1 if error.operation is ManagedCertificateOperation.RENEW else 0),
        )

    def _status_from_observation(
        self,
        inspection: ManagedCertificateInspection,
        *,
        state: ManagedCertificateState,
        detail: str,
        bootstrap_pending: bool = False,
        operation: ManagedCertificateOperation | None = None,
        failure_reason: ManagedCertificateFailureReason | None = None,
        retry_delay_seconds: float | None = None,
        last_checked_at=None,
        last_operation_at=None,
        last_success_at=None,
        last_failure_at=None,
        next_action_at=None,
        successful_issue_count: int | None = None,
        failed_issue_count: int | None = None,
        successful_renewal_count: int | None = None,
        failed_renewal_count: int | None = None,
    ) -> ManagedCertificateStatus:
        current = inspection.certificate
        return ManagedCertificateStatus(
            state=state,
            operation=operation if operation is not None else ManagedCertificateOperation.IDLE,
            failure_reason=(
                failure_reason if failure_reason is not None else ManagedCertificateFailureReason.NONE
            ),
            detail=detail,
            current_expires_at=current.not_valid_after if current is not None else None,
            last_checked_at=last_checked_at if last_checked_at is not None else self._status.last_checked_at,
            last_operation_at=last_operation_at if last_operation_at is not None else self._status.last_operation_at,
            last_success_at=last_success_at if last_success_at is not None else self._status.last_success_at,
            last_failure_at=last_failure_at if last_failure_at is not None else self._status.last_failure_at,
            next_action_at=next_action_at,
            retry_delay_seconds=retry_delay_seconds,
            certificate_present=current is not None,
            bootstrap_pending=bootstrap_pending,
            subject=current.subject if current is not None else None,
            issuer=current.issuer if current is not None else None,
            serial_number=current.serial_number if current is not None else None,
            successful_issue_count=(
                successful_issue_count if successful_issue_count is not None else self._status.successful_issue_count
            ),
            failed_issue_count=failed_issue_count if failed_issue_count is not None else self._status.failed_issue_count,
            successful_renewal_count=(
                successful_renewal_count
                if successful_renewal_count is not None
                else self._status.successful_renewal_count
            ),
            failed_renewal_count=(
                failed_renewal_count if failed_renewal_count is not None else self._status.failed_renewal_count
            ),
        )

    def _bootstrap_pending(self, record: GatewayEnrollmentRecord | None) -> bool:
        return bool(record and record.certificate_bootstrap and record.certificate_bootstrap.ott)

    def _sleep_seconds(self) -> float:
        base_delay = max(5.0, self.settings.step_ca_lifecycle_poll_interval_seconds)
        next_action_at = self._status.next_action_at
        if next_action_at is None:
            return base_delay
        seconds_until_next_action = (next_action_at - utc_now()).total_seconds()
        if seconds_until_next_action <= 0:
            return 1.0
        return min(base_delay, max(1.0, seconds_until_next_action))


class _LifecycleBackoff:
    def __init__(self, *, base_delay: float, max_delay: float) -> None:
        self.base_delay = max(0.5, base_delay)
        self.max_delay = max(self.base_delay, max_delay)
        self.failures = 0

    def reset(self) -> None:
        self.failures = 0

    def next_delay(self) -> float:
        self.failures += 1
        raw_delay = min(self.max_delay, self.base_delay * (2 ** (self.failures - 1)))
        jitter = raw_delay * 0.2 * random.random()
        return raw_delay + jitter


def _is_expired(certificate: ManagedCertificate) -> bool:
    if certificate.not_valid_after is None:
        return False
    return certificate.not_valid_after <= utc_now()


def _renewal_deadline(certificate: ManagedCertificate, skew_seconds: int):
    if certificate.not_valid_after is None:
        return None
    return certificate.not_valid_after - timedelta(seconds=skew_seconds)
