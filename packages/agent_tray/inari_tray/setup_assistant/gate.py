from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from inari.local_api.schemas import ManagedOnboardingStatusResponse
from PySide6.QtCore import QObject, QThreadPool, Signal

from .state import SetupAccess, SetupIntent, access_for_status

logger = logging.getLogger(__name__)


class SetupStatusClient(Protocol):
    def get_onboarding_status(self) -> ManagedOnboardingStatusResponse: ...


class SetupGate(QObject):
    """Routes installed-profile activations through authoritative setup state."""

    _resolved = Signal(int, object, object)
    _failed = Signal(int, object)

    def __init__(
        self,
        *,
        installed: bool,
        client: SetupStatusClient,
        show_invitation: Callable[[str | None], None],
        resume_setup: Callable[[ManagedOnboardingStatusResponse], None],
        show_unavailable: Callable[[str, Callable[[], None]], None],
        open_device_center: Callable[[], None],
    ) -> None:
        super().__init__()
        self._installed = installed
        self._client = client
        self._show_invitation = show_invitation
        self._resume_setup = resume_setup
        self._show_unavailable = show_unavailable
        self._open_device_center = open_device_center
        self._access = SetupAccess.UNKNOWN
        self._generation = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._resolved.connect(self._apply_status)
        self._failed.connect(self._apply_failure)

    @property
    def access(self) -> SetupAccess:
        return self._access

    def evaluate_background(self) -> None:
        if self._installed:
            self._resolve(SetupIntent.BACKGROUND)

    def begin(self, invitation: str | None = None) -> None:
        self._access = SetupAccess.REQUIRED
        self._show_invitation(invitation)

    def activate(self, invitation: str | None = None) -> None:
        if invitation is not None:
            self.begin(invitation)
            return
        if not self._installed or self._access is SetupAccess.COMPLETE:
            self._open_device_center()
            return
        self._resolve(SetupIntent.FOREGROUND)

    def complete(self, status: ManagedOnboardingStatusResponse) -> None:
        if status.completed_at is None:
            self._access = SetupAccess.REQUIRED
            self._resume_setup(status)
            return
        self._access = SetupAccess.COMPLETE
        self._open_device_center()

    def shutdown(self) -> None:
        self._generation += 1
        self._pool.clear()
        self._pool.waitForDone(2500)

    def _resolve(self, intent: SetupIntent) -> None:
        self._generation += 1
        generation = self._generation

        def resolve() -> None:
            try:
                status = self._client.get_onboarding_status()
            except Exception:
                logger.debug("Unable to resolve setup access", exc_info=True)
                self._failed.emit(generation, intent)
                return
            self._resolved.emit(generation, intent, status)

        self._pool.start(resolve)

    def _apply_status(
        self,
        generation: int,
        intent: SetupIntent,
        status: ManagedOnboardingStatusResponse,
    ) -> None:
        if generation != self._generation:
            return
        self._access = access_for_status(status)
        if self._access is SetupAccess.COMPLETE:
            if intent is SetupIntent.FOREGROUND:
                self._open_device_center()
            return
        self._resume_setup(status)

    def _apply_failure(self, generation: int, intent: SetupIntent) -> None:
        if generation != self._generation:
            return
        self._show_unavailable(
            "The Inari device service is not available yet. Start or repair the "
            "service, then try again.",
            lambda: self._resolve(intent),
        )
