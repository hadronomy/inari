from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from inari.local_api.schemas import (
    DeviceResponse,
    ManagedOnboardingStatusResponse,
)
from inari_tray.setup_assistant.presenter import SetupPresenter
from inari_tray.setup_assistant.state import SetupStep
from PySide6.QtCore import QCoreApplication


@pytest.fixture(scope="module")
def qt_application() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


class PresenterClient:
    def __init__(self) -> None:
        self.status = onboarding_status()
        self.confirmations: list[
            tuple[tuple[str, ...], dict[str, str], str | None]
        ] = []
        self.cancel_calls = 0

    def preview_onboarding(self, invitation: str, *, controller_url=None):
        raise AssertionError("not used by this test")

    def start_onboarding(self, invitation: str, *, controller_url=None):
        raise AssertionError("not used by this test")

    def get_onboarding_status(self) -> ManagedOnboardingStatusResponse:
        return self.status

    def confirm_onboarding_devices(
        self,
        *,
        device_ids: tuple[str, ...],
        labels: dict[str, str],
        default_printer_device_id: str | None,
    ) -> ManagedOnboardingStatusResponse:
        self.confirmations.append((device_ids, labels, default_printer_device_id))
        return onboarding_status(completed=True)

    def cancel_onboarding(self) -> ManagedOnboardingStatusResponse:
        self.cancel_calls += 1
        return onboarding_status()

    def submit_test_page(self, *, device_id=None, printer_name=None) -> object:
        return object()


class PresenterBridge:
    def __init__(self) -> None:
        self.restart_calls = 0

    def restart(self) -> str:
        self.restart_calls += 1
        return "Restarted Inari."


class PresenterView:
    def __init__(self) -> None:
        self.invitation_pages: list[tuple[str | None, str | None]] = []
        self.connection_pages: list[tuple[SetupStep, str, str | None, bool]] = []
        self.device_pages: list[list[DeviceResponse]] = []
        self.ready_pages: list[ManagedOnboardingStatusResponse] = []
        self.confirm_busy: list[bool] = []
        self.cancel_busy: list[bool] = []
        self.dismiss_calls = 0

    def show_invitation_page(
        self, *, invitation: str | None = None, error: str | None = None
    ) -> None:
        self.invitation_pages.append((invitation, error))

    def show_connection_page(
        self,
        *,
        step: SetupStep,
        message: str,
        error: str | None = None,
        retry: Callable[[], None] | None = None,
        allow_start_over: bool = False,
    ) -> None:
        del retry
        self.connection_pages.append((step, message, error, allow_start_over))

    def show_devices_page(self, devices: list[DeviceResponse]) -> None:
        self.device_pages.append(devices)

    def show_ready_page(self, status: ManagedOnboardingStatusResponse) -> None:
        self.ready_pages.append(status)

    def update_details(self, value: object) -> None:
        del value

    def set_confirm_busy(self, busy: bool) -> None:
        self.confirm_busy.append(busy)

    def set_cancel_busy(self, busy: bool) -> None:
        self.cancel_busy.append(busy)

    def dismiss_view(self) -> None:
        self.dismiss_calls += 1


def test_invalid_preview_returns_to_editable_invitation_page() -> None:
    presenter, _, _, view = make_presenter()
    presenter.begin("invalid invitation", None)
    generation = presenter._generation

    presenter._task_failed(
        generation,
        "preview",
        "This does not look like a valid Inari invitation.",
    )

    assert view.invitation_pages[-1] == (
        "invalid invitation",
        "This does not look like a valid Inari invitation.",
    )
    presenter.shutdown()


def test_restart_required_state_resumes_by_restarting_agent(
    qt_application: QCoreApplication,
) -> None:
    presenter, _, bridge, view = make_presenter()
    status = onboarding_status(
        phase="restart_required",
        restart_required=True,
    )

    presenter.resume(status)
    process_until(qt_application, lambda: bridge.restart_calls == 1)

    assert view.connection_pages[0][0] is SetupStep.SECURING
    presenter.shutdown()


def test_failed_state_offers_transactional_start_over(
    qt_application: QCoreApplication,
) -> None:
    presenter, client, _, view = make_presenter()
    presenter.resume(
        onboarding_status(
            phase="failed",
            detail="Enrollment failed",
            last_error="The invitation has expired.",
        )
    )

    assert view.connection_pages[-1] == (
        SetupStep.FAILED,
        "Enrollment failed",
        "The invitation has expired.",
        True,
    )
    presenter.start_over()
    process_until(qt_application, lambda: client.cancel_calls == 1)
    process_until(qt_application, lambda: bool(view.invitation_pages))
    assert view.dismiss_calls == 0
    presenter.shutdown()


def test_empty_device_confirmation_persists_completion_before_ready(
    qt_application: QCoreApplication,
) -> None:
    presenter, client, _, view = make_presenter()

    presenter.confirm_devices(
        device_ids=(),
        labels={},
        default_printer_device_id=None,
    )
    process_until(qt_application, lambda: bool(view.ready_pages))

    assert client.confirmations == [((), {}, None)]
    assert view.ready_pages[0].completed_at is not None
    assert view.confirm_busy == [True, False]
    presenter.shutdown()


def test_result_from_closed_or_superseded_attempt_is_ignored() -> None:
    presenter, _, _, view = make_presenter()
    presenter.begin("first invitation", None)
    stale_generation = presenter._generation
    presenter.present_invitation("second invitation")

    presenter._task_failed(
        stale_generation,
        "preview",
        "A stale request failed.",
    )

    assert view.invitation_pages == [("second invitation", None)]
    presenter.shutdown()


def make_presenter() -> tuple[
    SetupPresenter, PresenterClient, PresenterBridge, PresenterView
]:
    client = PresenterClient()
    bridge = PresenterBridge()
    view = PresenterView()
    presenter = SetupPresenter(
        client=client,
        bridge=bridge,
        view=view,
        on_complete=None,
    )
    return presenter, client, bridge, view


def onboarding_status(
    *,
    phase: str = "not_started",
    detail: str = "Setup is required",
    restart_required: bool = False,
    last_error: str | None = None,
    completed: bool = False,
) -> ManagedOnboardingStatusResponse:
    return ManagedOnboardingStatusResponse.model_validate(
        {
            "phase": "ready" if completed else phase,
            "detail": "Ready" if completed else detail,
            "restart_required": restart_required,
            "last_error": last_error,
            "completed_at": datetime.now(UTC) if completed else None,
        }
    )


def process_until(
    application: QCoreApplication,
    condition: Callable[[], bool],
    *,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert condition()
