from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

import httpx
from inari.local_api.schemas import (
    DeviceResponse,
    ManagedOnboardingPreviewResponse,
    ManagedOnboardingStartResponse,
    ManagedOnboardingStatusResponse,
)
from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from .state import SetupStep, step_for_status

logger = logging.getLogger(__name__)


class SetupClient(Protocol):
    def preview_onboarding(
        self, invitation: str, *, controller_url: str | None = None
    ) -> ManagedOnboardingPreviewResponse: ...

    def start_onboarding(
        self, invitation: str, *, controller_url: str | None = None
    ) -> ManagedOnboardingStartResponse: ...

    def get_onboarding_status(self) -> ManagedOnboardingStatusResponse: ...

    def confirm_onboarding_devices(
        self,
        *,
        device_ids: tuple[str, ...],
        labels: dict[str, str],
        default_printer_device_id: str | None,
    ) -> ManagedOnboardingStatusResponse: ...

    def cancel_onboarding(self) -> ManagedOnboardingStatusResponse: ...

    def submit_test_page(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> object: ...


class SetupBridge(Protocol):
    def restart(self) -> str: ...


class SetupView(Protocol):
    def show_invitation_page(
        self, *, invitation: str | None = None, error: str | None = None
    ) -> None: ...

    def show_connection_page(
        self,
        *,
        step: SetupStep,
        message: str,
        error: str | None = None,
        retry: Callable[[], None] | None = None,
        allow_start_over: bool = False,
    ) -> None: ...

    def show_devices_page(self, devices: list[DeviceResponse]) -> None: ...

    def show_ready_page(self, status: ManagedOnboardingStatusResponse) -> None: ...

    def update_details(self, value: object) -> None: ...

    def set_confirm_busy(self, busy: bool) -> None: ...

    def set_cancel_busy(self, busy: bool) -> None: ...

    def dismiss_view(self) -> None: ...


class SetupPresenter(QObject):
    _completed = Signal(int, str, object)
    _failed = Signal(int, str, str)

    def __init__(
        self,
        *,
        client: SetupClient,
        bridge: SetupBridge,
        view: SetupView,
        on_complete: Callable[[ManagedOnboardingStatusResponse], None] | None,
    ) -> None:
        super().__init__()
        self._client = client
        self._bridge = bridge
        self._view = view
        self._on_complete = on_complete
        self._generation = 0
        self._status_in_flight = False
        self._invitation = ""
        self._controller_url: str | None = None
        self._completion: ManagedOnboardingStatusResponse | None = None
        self._close_after_cancel = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self.check_status)
        self._completed.connect(self._task_completed)
        self._failed.connect(self._task_failed)

    def present_invitation(self, invitation: str | None = None) -> None:
        self.suspend()
        self._completion = None
        self._invitation = invitation or ""
        self._controller_url = None
        self._view.show_invitation_page(invitation=invitation)

    def begin(self, invitation: str, controller_url: str | None) -> None:
        self.suspend()
        self._invitation = invitation
        self._controller_url = controller_url
        self._view.show_connection_page(
            step=SetupStep.CHECKING,
            message="Checking this computer",
        )
        self._run(
            "preview",
            lambda: self._client.preview_onboarding(
                invitation, controller_url=controller_url
            ),
        )

    def resume(self, status: ManagedOnboardingStatusResponse) -> None:
        self.suspend()
        self._apply_status(status)

    def unavailable(self, message: str, retry: Callable[[], None]) -> None:
        self.suspend()
        self._view.show_connection_page(
            step=SetupStep.CHECKING,
            message="Checking this computer",
            error=message,
            retry=retry,
        )

    def check_status(self) -> None:
        if self._status_in_flight:
            return
        self._status_in_flight = True
        self._run("status", self._client.get_onboarding_status)

    def confirm_devices(
        self,
        *,
        device_ids: tuple[str, ...],
        labels: dict[str, str],
        default_printer_device_id: str | None,
    ) -> None:
        self._view.set_confirm_busy(True)
        self._run(
            "confirm",
            lambda: self._client.confirm_onboarding_devices(
                device_ids=device_ids,
                labels=labels,
                default_printer_device_id=default_printer_device_id,
            ),
        )

    def test_device(self, device_id: str) -> None:
        self._run(
            "test",
            lambda: self._client.submit_test_page(device_id=device_id),
        )

    def cancel(self) -> None:
        self._close_after_cancel = True
        self._cancel()

    def start_over(self) -> None:
        self._close_after_cancel = False
        self._cancel()

    def finish(self) -> None:
        status = self._completion
        if status is None or status.completed_at is None:
            self._view.show_connection_page(
                step=SetupStep.FAILED,
                message="Setup is not complete",
                error="Finish the device step before opening Device Center.",
                allow_start_over=True,
            )
            return
        self._view.dismiss_view()
        if self._on_complete is not None:
            self._on_complete(status)

    def suspend(self) -> None:
        self._poll_timer.stop()
        self._generation += 1
        self._status_in_flight = False
        self._pool.clear()

    def shutdown(self) -> None:
        self.suspend()
        self._pool.waitForDone(5500)

    def _cancel(self) -> None:
        self._poll_timer.stop()
        self._view.set_cancel_busy(True)
        self._run("cancel", self._client.cancel_onboarding)

    def _start_after_preview(self, preview: ManagedOnboardingPreviewResponse) -> None:
        self._view.update_details(preview)
        self._view.show_connection_page(
            step=SetupStep.SECURING,
            message="Securing the connection",
        )
        self._run(
            "start",
            lambda: self._client.start_onboarding(
                self._invitation, controller_url=self._controller_url
            ),
        )

    def _start_polling(self) -> None:
        self._poll_timer.start()
        self.check_status()

    def _apply_status(self, status: ManagedOnboardingStatusResponse) -> None:
        self._view.update_details(status)
        if status.restart_required or status.phase == "restart_required":
            self._view.show_connection_page(
                step=SetupStep.SECURING,
                message="Restarting Inari securely",
            )
            self._run("restart", self._bridge.restart)
            return
        step = step_for_status(status)
        if step is SetupStep.INVITATION:
            self._view.show_invitation_page()
            return
        if step is SetupStep.FAILED:
            self._view.show_connection_page(
                step=step,
                message=status.detail,
                error=status.last_error or status.detail,
                allow_start_over=True,
            )
            return
        if step is SetupStep.DEVICES:
            self._view.show_devices_page(status.devices)
            return
        if step is SetupStep.READY:
            self._completion = status
            self._view.show_ready_page(status)
            return
        self._view.show_connection_page(step=step, message=status.detail)
        self._poll_timer.start()

    def _run(self, operation: str, task: Callable[[], object]) -> None:
        self._generation += 1
        generation = self._generation

        def execute() -> None:
            try:
                result = task()
            except Exception as exc:
                logger.debug("Setup operation failed", exc_info=True)
                self._failed.emit(generation, operation, friendly_error(exc))
                return
            self._completed.emit(generation, operation, result)

        self._pool.start(execute)

    def _task_completed(self, generation: int, operation: str, result: object) -> None:
        if generation != self._generation:
            return
        if operation == "preview" and isinstance(
            result, ManagedOnboardingPreviewResponse
        ):
            self._start_after_preview(result)
            return
        if operation == "start" and isinstance(result, ManagedOnboardingStartResponse):
            self._view.update_details(result)
            if result.restart_required:
                self._view.show_connection_page(
                    step=SetupStep.SECURING,
                    message="Restarting Inari securely",
                )
                self._run("restart", self._bridge.restart)
            else:
                self._start_polling()
            return
        if operation == "restart":
            self._start_polling()
            return
        if operation == "status" and isinstance(
            result, ManagedOnboardingStatusResponse
        ):
            self._status_in_flight = False
            self._apply_status(result)
            return
        if operation == "confirm" and isinstance(
            result, ManagedOnboardingStatusResponse
        ):
            self._view.set_confirm_busy(False)
            if result.completed_at is None:
                self._view.show_connection_page(
                    step=SetupStep.FAILED,
                    message="Setup is not complete",
                    error="The agent did not persist the setup confirmation.",
                    allow_start_over=True,
                )
                return
            self._completion = result
            self._view.show_ready_page(result)
            return
        if operation == "cancel":
            self._view.set_cancel_busy(False)
            if self._close_after_cancel:
                self._view.dismiss_view()
            else:
                self.present_invitation()

    def _task_failed(self, generation: int, operation: str, message: str) -> None:
        if generation != self._generation:
            return
        if operation == "status":
            self._status_in_flight = False
        if operation == "confirm":
            self._view.set_confirm_busy(False)
        if operation == "cancel":
            self._view.set_cancel_busy(False)
        self._poll_timer.stop()
        if operation == "preview":
            self._view.show_invitation_page(
                invitation=self._invitation,
                error=message,
            )
            return
        self._view.show_connection_page(
            step=SetupStep.FAILED,
            message="Setup could not continue",
            error=message,
            retry=self.check_status if operation == "status" else None,
            allow_start_over=operation not in {"status", "cancel"},
        )


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            payload = exc.response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail")
        if not isinstance(detail, str):
            error = payload.get("error")
            detail = error.get("message") if isinstance(error, dict) else None
        if isinstance(detail, str) and detail:
            return detail
    message = str(exc).strip()
    return message or "Inari could not complete this step."
