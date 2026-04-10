from functools import lru_cache

from fastapi import Depends

from .config import AgentSettings
from .printer_service import PrinterService


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()


def get_printer_service(settings: AgentSettings = Depends(get_settings)) -> PrinterService:
    return PrinterService(settings=settings)
