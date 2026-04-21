from __future__ import annotations

from typing import Protocol

from inari.local_api.schemas import (
    DeviceDirectoryResponse,
    DeviceEventCollectionResponse,
    JobResourceResponse,
)


class DeviceCenterClient(Protocol):
    def list_devices(self) -> DeviceDirectoryResponse: ...

    def list_device_events(
        self, device_id: str, *, limit: int = 50
    ) -> DeviceEventCollectionResponse: ...

    def submit_test_page(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> JobResourceResponse: ...

    def open_cash_drawer(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> JobResourceResponse: ...
