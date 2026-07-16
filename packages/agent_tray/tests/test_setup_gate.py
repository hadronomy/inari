from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from inari.local_api.schemas import ManagedOnboardingStatusResponse
from inari_tray.setup_assistant.gate import SetupGate
from inari_tray.setup_assistant.state import SetupAccess, SetupIntent
from PySide6.QtCore import QCoreApplication


@pytest.fixture(scope="module")
def qt_application() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


class StatusClient:
    def __init__(
        self,
        status: ManagedOnboardingStatusResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.calls = 0

    def get_onboarding_status(self) -> ManagedOnboardingStatusResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.status is not None
        return self.status


class GateEvents:
    def __init__(self) -> None:
        self.invitations: list[str | None] = []
        self.resumed: list[ManagedOnboardingStatusResponse] = []
        self.unavailable: list[tuple[str, Callable[[], None]]] = []
        self.device_center_opens = 0

    def show_invitation(self, invitation: str | None) -> None:
        self.invitations.append(invitation)

    def resume(self, status: ManagedOnboardingStatusResponse) -> None:
        self.resumed.append(status)

    def show_unavailable(self, message: str, retry: Callable[[], None]) -> None:
        self.unavailable.append((message, retry))

    def open_device_center(self) -> None:
        self.device_center_opens += 1


def test_relaunch_after_incomplete_setup_cannot_open_device_center(
    qt_application: QCoreApplication,
) -> None:
    status = onboarding_status()
    client = StatusClient(status)
    events = GateEvents()
    gate = setup_gate(client, events)

    gate.activate()
    process_until(qt_application, lambda: bool(events.resumed))

    assert gate.access is SetupAccess.REQUIRED
    assert events.resumed == [status]
    assert events.device_center_opens == 0
    gate.shutdown()


def test_reactivation_refreshes_incomplete_progress(
    qt_application: QCoreApplication,
) -> None:
    client = StatusClient(onboarding_status())
    events = GateEvents()
    gate = setup_gate(client, events)
    gate.activate()
    process_until(qt_application, lambda: len(events.resumed) == 1)
    client.status = ManagedOnboardingStatusResponse(
        phase="finding_devices",
        detail="Choose devices",
    )

    gate.activate()
    process_until(qt_application, lambda: len(events.resumed) == 2)

    assert events.resumed[-1].phase == "finding_devices"
    assert client.calls == 2
    gate.shutdown()


def test_completed_setup_opens_device_center_after_relaunch(
    qt_application: QCoreApplication,
) -> None:
    status = onboarding_status(completed=True)
    events = GateEvents()
    gate = setup_gate(StatusClient(status), events)

    gate.activate()
    process_until(qt_application, lambda: events.device_center_opens == 1)

    assert gate.access is SetupAccess.COMPLETE
    assert events.resumed == []
    gate.shutdown()


def test_background_startup_only_opens_incomplete_setup(
    qt_application: QCoreApplication,
) -> None:
    incomplete_events = GateEvents()
    incomplete = setup_gate(StatusClient(onboarding_status()), incomplete_events)
    complete_events = GateEvents()
    complete = setup_gate(
        StatusClient(onboarding_status(completed=True)), complete_events
    )

    incomplete.evaluate_background()
    complete.evaluate_background()
    process_until(qt_application, lambda: bool(incomplete_events.resumed))
    process_until(qt_application, lambda: complete.access is SetupAccess.COMPLETE)

    assert incomplete_events.device_center_opens == 0
    assert complete_events.device_center_opens == 0
    assert complete_events.resumed == []
    incomplete.shutdown()
    complete.shutdown()


def test_unavailable_agent_fails_closed_and_can_retry(
    qt_application: QCoreApplication,
) -> None:
    client = StatusClient(error=ConnectionError("service unavailable"))
    events = GateEvents()
    gate = setup_gate(client, events)

    gate.activate()
    process_until(qt_application, lambda: bool(events.unavailable))

    assert gate.access is SetupAccess.UNKNOWN
    assert events.device_center_opens == 0
    client.error = None
    client.status = onboarding_status()
    events.unavailable[0][1]()
    process_until(qt_application, lambda: bool(events.resumed))
    assert gate.access is SetupAccess.REQUIRED
    gate.shutdown()


def test_deep_link_always_opens_setup_even_after_completion() -> None:
    events = GateEvents()
    gate = setup_gate(StatusClient(onboarding_status(completed=True)), events)
    gate.complete(onboarding_status(completed=True))
    events.device_center_opens = 0

    gate.activate("inari://enroll?invite_id=example#code=secret")

    assert events.device_center_opens == 0
    assert events.invitations == ["inari://enroll?invite_id=example#code=secret"]
    gate.shutdown()


def test_development_profile_bypasses_setup_gate() -> None:
    client = StatusClient(onboarding_status())
    events = GateEvents()
    gate = setup_gate(client, events, installed=False)

    gate.activate()

    assert client.calls == 0
    assert events.device_center_opens == 1
    gate.shutdown()


def test_stale_status_result_cannot_change_access() -> None:
    events = GateEvents()
    gate = setup_gate(StatusClient(onboarding_status()), events)
    gate._generation = 2

    gate._apply_status(1, SetupIntent.FOREGROUND, onboarding_status(completed=True))

    assert gate.access is SetupAccess.UNKNOWN
    assert events.device_center_opens == 0
    gate.shutdown()


def setup_gate(
    client: StatusClient,
    events: GateEvents,
    *,
    installed: bool = True,
) -> SetupGate:
    return SetupGate(
        installed=installed,
        client=client,
        show_invitation=events.show_invitation,
        resume_setup=events.resume,
        show_unavailable=events.show_unavailable,
        open_device_center=events.open_device_center,
    )


def onboarding_status(*, completed: bool = False) -> ManagedOnboardingStatusResponse:
    return ManagedOnboardingStatusResponse(
        phase="ready" if completed else "not_started",
        detail="Ready" if completed else "Setup is required",
        completed_at=datetime.now(UTC) if completed else None,
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
