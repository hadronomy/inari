from __future__ import annotations

import platform

from dishka import Provider, Scope, provide

from ..config import AgentSettings
from ..drivers import DriverRegistry
from ..printing.drivers import (
    CupsPrinterDriver,
    RawSocketPrinterDriver,
    WindowsPrinterDriver,
    WindowsSpooler,
)
from ..printing.service import PrinterService


def build_printer_drivers(
    settings: AgentSettings, *, platform_system: str | None = None
) -> tuple:
    current_platform = platform_system or platform.system()
    drivers = []
    if current_platform == "Windows":
        drivers.append(
            WindowsPrinterDriver(
                spooler=WindowsSpooler(),
            )
        )
    elif current_platform in {"Linux", "Darwin"}:
        drivers.append(CupsPrinterDriver())
    if settings.network_printers:
        drivers.append(
            RawSocketPrinterDriver(
                configured_printers=tuple(settings.network_printers),
            )
        )
    return tuple(drivers)


class DriverProvider(Provider):
    scope = Scope.APP

    @provide
    def driver_registry(self, settings: AgentSettings) -> DriverRegistry:
        return DriverRegistry(drivers=build_printer_drivers(settings))

    @provide
    def printer_service(
        self,
        settings: AgentSettings,
        driver_registry: DriverRegistry,
    ) -> PrinterService:
        return PrinterService(
            settings=settings,
            driver_registry=driver_registry,
        )
