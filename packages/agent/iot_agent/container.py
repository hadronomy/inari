from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .config import AgentSettings, get_settings
from .drivers import DriverRegistry
from .drivers.printers import WindowsPrinterDriver, WindowsSpooler
from .printers import PrinterTransport
from .printer_service import PrinterService
from .receipt_renderers import EscPosRenderer


@dataclass(slots=True, frozen=True)
class AgentContainer:
    settings: AgentSettings
    driver_registry: DriverRegistry
    printer_service: PrinterService


def build_container(settings: AgentSettings) -> AgentContainer:
    driver_registry = DriverRegistry(
        drivers=(
            WindowsPrinterDriver(
                spooler=WindowsSpooler(),
                default_transport=PrinterTransport(settings.default_printer_mode),
            ),
        )
    )
    printer_service = PrinterService(
        settings=settings,
        driver_registry=driver_registry,
        escpos_renderer=EscPosRenderer(),
    )
    return AgentContainer(
        settings=settings,
        driver_registry=driver_registry,
        printer_service=printer_service,
    )


@lru_cache(maxsize=1)
def get_default_container() -> AgentContainer:
    return build_container(get_settings())
