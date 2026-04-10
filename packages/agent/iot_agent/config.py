from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
PrinterMode = Literal["auto", "raw", "shell"]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ODOO_IOT_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 7310
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8069",
            "http://localhost:8069",
        ]
    )
    default_printer_name: str | None = None
    log_level: LogLevel = "INFO"
    html_print_enabled: bool = True
    default_printer_mode: PrinterMode = "auto"
    temp_dir: str = "./tmp"
    log_dir: str = "./logs"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def normalize_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    return AgentSettings()
