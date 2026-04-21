from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Iterable, cast

from .base import DeviceDriver, DeviceKind
from ..printing.drivers.base import PrinterDriver


class DriverRegistry:
    """Central registry for concrete device drivers."""

    def __init__(self, drivers: Iterable[DeviceDriver] = ()) -> None:
        self._drivers: DefaultDict[DeviceKind, list[DeviceDriver]] = defaultdict(list)
        for driver in drivers:
            self.register(driver)

    def register(self, driver: DeviceDriver) -> None:
        bucket = self._drivers[driver.metadata.kind]
        if any(existing.metadata.key == driver.metadata.key for existing in bucket):
            raise ValueError(f"Driver {driver.metadata.key!r} is already registered.")
        bucket.append(driver)

    def drivers_for(
        self, kind: DeviceKind, *, available_only: bool = False
    ) -> tuple[DeviceDriver, ...]:
        drivers = tuple(self._drivers.get(kind, ()))
        if not available_only:
            return drivers
        return tuple(driver for driver in drivers if driver.is_available())

    def printer_drivers(
        self, *, available_only: bool = False
    ) -> tuple[PrinterDriver, ...]:
        drivers = self.drivers_for(DeviceKind.PRINTER, available_only=available_only)
        return tuple(cast(PrinterDriver, driver) for driver in drivers)
