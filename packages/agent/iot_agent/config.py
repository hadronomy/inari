from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
PrinterMode = Literal["auto", "raw", "text", "document"]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IOT_AGENT_",
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
    temp_dir: Path = Path("./tmp")
    log_dir: Path = Path("./logs")
    runtime_database_path: Path = Path("./data/iot-agent.sqlite3")
    discovery_poll_interval_seconds: float = 3.0
    scheduler_poll_interval_seconds: float = 0.5
    scheduler_batch_size: int = 32
    job_max_attempts: int = 3
    job_retry_base_delay_seconds: int = 2
    job_retry_max_delay_seconds: int = 30
    job_dispatch_lease_seconds: int = 15
    job_execution_lease_seconds: int = 30
    job_heartbeat_interval_seconds: float = 5.0
    job_execution_timeout_seconds: float = 60.0
    job_lease_recovery_interval_seconds: float = 5.0

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def normalize_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("temp_dir", "log_dir", "runtime_database_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value)
        return value


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    return AgentSettings()
